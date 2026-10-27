"""Official-SDK stdio MCP bridge. No arbitrary server paths or remote URL fetches."""
import base64
import binascii
import logging
import os
import time
import uuid
from typing import Any

import httpx
from mcp.server import MCPServer
from mcp_types import ToolAnnotations


def create_mcp(api_url=None, api_token=None, transport=None, server=None):
    api_url = (api_url or os.getenv("DAVALLKA_API_URL", "http://127.0.0.1:80")).rstrip("/")
    api_token = api_token or os.getenv("DAVALLKA_MCP_API_TOKEN")
    if not api_token:
        raise RuntimeError("DAVALLKA_MCP_API_TOKEN is required")
    if not api_url.startswith(("https://", "http://127.0.0.1:", "http://localhost:")):
        raise RuntimeError("API URL must use HTTPS or explicit loopback HTTP")
    server = server or MCPServer("davallka-images")

    def asset_path(image_id):
        try:
            return "/" + str(uuid.UUID(image_id))
        except (ValueError, TypeError, AttributeError):
            raise ValueError("A valid image UUID is required") from None

    def call(method, path, **kwargs):
        try:
            with httpx.Client(base_url=api_url, headers={"Authorization": "Bearer " + api_token}, timeout=60, follow_redirects=False, transport=transport, trust_env=False) as client:
                response = client.request(method, "/api/v1/images" + path, **kwargs)
            if response.status_code >= 400:
                try:
                    message = response.json()["error"]["message"]
                except (ValueError, KeyError, TypeError):
                    message = "Request failed"
                raise ValueError(f"API {response.status_code}: {message}")
            return response.json()
        except httpx.HTTPError:
            raise ValueError("Image service could not be reached") from None

    read = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
    write = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
    destructive = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False)

    @server.tool(annotations=write, structured_output=True)
    def upload_image(image_base64: str, name: str, idempotency_key: str, path: str | None = None) -> dict[str, Any]:
        """Upload a raster image (base64, at most 10 MiB decoded); return ID and URLs.

        Reuse the same idempotency_key only for retries of identical input.
        Read laptop files on the client and encode their bytes; paths on this server are not accepted.
        """
        if len(image_base64) > 4 * ((10 * 1024**2 + 2) // 3):
            raise ValueError("MCP uploads are limited to 10 MiB; use multipart API for larger files")
        try:
            content = base64.b64decode(image_base64, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError("Invalid base64 image data") from None
        if len(content) > 10 * 1024**2 or not idempotency_key:
            raise ValueError("An idempotency key and image of at most 10 MiB are required")
        data = {"name": name}
        if path is not None:
            data["path"] = path
        return call("POST", "", data=data, files={"file": (name, content, "application/octet-stream")}, headers={"Idempotency-Key": idempotency_key})

    @server.tool(annotations=read, structured_output=True)
    def get_image(image_id: str) -> dict[str, Any]:
        """Get current image metadata, version and canonical URLs by stable ID."""
        return call("GET", asset_path(image_id))

    @server.tool(annotations=read, structured_output=True)
    def list_images(limit: int = 50, offset: int = 0, folder: str | None = None, deleted: bool = False) -> dict[str, Any]:
        """List a page of raster images; include deleted=True for recoverable images."""
        params = {"limit": limit, "offset": offset, "deleted": str(deleted).lower()}
        if folder is not None:
            params["folder"] = folder
        return call("GET", "", params=params)

    @server.tool(annotations=write, structured_output=True)
    def rename_image(image_id: str, new_name: str, expected_version: int) -> dict[str, Any]:
        """Change display/download name; keep the published image URL unchanged."""
        return call("POST", asset_path(image_id) + "/rename", json={"name": new_name, "expected_version": expected_version})

    @server.tool(annotations=write, structured_output=True)
    def set_image_path(image_id: str, new_path: str, expected_version: int, keep_old_alias: bool = True) -> dict[str, Any]:
        """Change canonical relative URL path; explicitly retain or revoke the previous URL."""
        return call("POST", asset_path(image_id) + "/path", json={"path": new_path, "expected_version": expected_version, "keep_old_alias": keep_old_alias})

    @server.tool(annotations=destructive, structured_output=True)
    def delete_image(image_id: str, expected_version: int) -> dict[str, Any]:
        """Soft-delete this exact image ID/version and disable all its URLs. Bytes remain recoverable."""
        return call("DELETE", asset_path(image_id), json={"expected_version": expected_version})

    @server.tool(annotations=write, structured_output=True)
    def restore_image(image_id: str, expected_version: int) -> dict[str, Any]:
        """Restore a soft-deleted image and its non-revoked aliases."""
        return call("POST", asset_path(image_id) + "/restore", json={"expected_version": expected_version})

    @server.tool(annotations=read, structured_output=True)
    def list_cleanup_candidates(older_than_days: int, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        """Preview old image IDs and versions. Age is not evidence that an image is unused.

        This tool does not delete anything. Review external references before individual deletion.
        """
        if older_than_days < 1:
            raise ValueError("Age must be at least one day")
        return call("GET", "", params={"older_than": time.time() - older_than_days * 86400, "limit": limit, "offset": offset})

    return server


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    create_mcp().run(transport="stdio")
