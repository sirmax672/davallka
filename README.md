## 🧩 d'Avallka

<p align="center">
  <img src="/images/davallka.png" alt="d'avallka logo" width="160"/>
</p>

**d'Avallka** is a simple, lightweight file-sharing service built for developers.  
It lets authenticated users upload files — and instantly makes them available to everyone through direct web links.

Perfect for hosting demo assets, test data, or any content your web apps need to access publicly without the hassle of renaming or complex permissions.

### ✨ Key Features
- **Authenticated uploads** – only registered users can upload files  
- **Public access** – once uploaded, files are available via clean, direct URLs  
- **Developer-friendly** – ideal for quick web demos, API prototyping, and content testing  
- **Minimalist UI** – upload, copy link, done  

## Features

- 📁 **File Upload**: Upload files with drag-and-drop interface
- 📥 **File Download**: Download files directly from the web interface
- 🗑️ **File Management**: View all uploaded files and delete them
- 🎨 **Modern UI**: Beautiful, responsive web interface
- 🐳 **Docker Ready**: Easy deployment with Docker and Docker Compose
- 📱 **Mobile Friendly**: Responsive design that works on all devices

## Quick Start

### Using Docker Compose (Recommended)

1. **Clone or download this repository**
2. **Run the application**:
   ```bash
   docker-compose up -d
   ```
3. **Open your browser** and go to: `http://localhost:80`

### Using Environment Variables

You can customize the application by setting environment variables:

```bash
# Set custom credentials and port
export ADMIN_USERNAME=myuser
export ADMIN_PASSWORD=mypassword
export PORT=8080
docker-compose up -d
```

Or create a `.env` file:
```bash
cp env.example .env
# Edit .env with your values
docker-compose up -d
```

### Using Docker

1. **Build the image**:
   ```bash
   docker build -t davallka .
   ```
2. **Run the container**:
   ```bash
   # Default port 80
   docker run -d -p 80:80 -v $(pwd)/uploads:/app/uploads davallka
   
   # Custom port and credentials
   docker run -d -p 8080:8080 \
     -e PORT=8080 \
     -e ADMIN_USERNAME=myuser \
     -e ADMIN_PASSWORD=mypassword \
     -v $(pwd)/uploads:/app/uploads davallka
   ```
3. **Open your browser** and go to: `http://localhost:80` (or your custom port)

### Local Development

1. **Install Python dependencies**:
   ```bash
   pip install -r requirements.txt
   ```
2. **Set environment variables** (optional):
   ```bash
   export ADMIN_USERNAME=myuser
   export ADMIN_PASSWORD=mypassword
   export PORT=8080
   ```
3. **Run the application**:
   ```bash
   python app.py
   ```
4. **Open your browser** and go to: `http://localhost:80` (or your custom port)

## Usage

### Uploading Files
1. Click on "Choose File" button
2. Select the file you want to upload
3. Click "Upload File" button
4. The file will be uploaded and stored with its original name

### Downloading Files
1. Find the file in the files list
2. Click the "📥 Download" button
3. The file will be downloaded to your device

### Deleting Files
1. Find the file you want to delete
2. Click the "🗑️ Delete" button
3. Confirm the deletion in the popup dialog

## Configuration

### Environment Variables

The application supports the following environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `ADMIN_USERNAME` | `admin` | Username for admin login |
| `ADMIN_PASSWORD` | `password123` | Password for admin login |
| `PORT` | `80` | Port for the web server |
| `FLASK_SECRET_KEY` | `your-secret-key-change-this` | Secret key for Flask sessions |

### File Size Limit
The default maximum file size is 100MB. You can change this in `app.py`:
```python
MAX_CONTENT_LENGTH = 100 * 1024 * 1024  # 100MB
```

### Allowed File Types
By default, all file types are allowed. You can restrict file types by modifying the `ALLOWED_EXTENSIONS` set in `app.py`:
```python
ALLOWED_EXTENSIONS = {'txt', 'pdf', 'png', 'jpg', 'jpeg', 'gif'}
```

## File Storage

- Files are stored in the `uploads/` directory
- Files retain their original names
- If a file with the same name already exists, a number suffix is added (e.g., `file_1.txt`)
- Files are persistent across container restarts when using Docker volumes

## API Endpoints

The application also provides a simple API:

- `GET /api/files` - Returns a JSON list of all uploaded files with metadata

## Security Notes

- The application is designed for simple file sharing and should not be used in production without additional security measures
- Consider adding authentication, file type validation, and virus scanning for production use
- The secret key should be changed in production environments

## Troubleshooting

### Port Already in Use
If port 80 is already in use, you can change it using environment variables:
```bash
# Using docker-compose
PORT=8080 docker-compose up -d

# Using docker run
docker run -d -p 8080:8080 -e PORT=8080 davallka
```

### Permission Issues
If you encounter permission issues with the uploads directory:
```bash
sudo chown -R $USER:$USER uploads/
```

### Container Won't Start
Check the logs:
```bash
docker-compose logs filestorage
```

## Development

### Project Structure
```
davallka/
├── app.py              # Main Flask application
├── requirements.txt    # Python dependencies
├── Dockerfile         # Docker configuration
├── docker-compose.yml # Docker Compose configuration
├── templates/         # HTML templates
│   └── index.html    # Main web interface
├── uploads/          # File storage directory (created automatically)
└── README.md         # This file
```

### Adding Features
The application is built with Flask and uses a simple file-based storage system. You can easily extend it by:
- Adding user authentication
- Implementing file categories
- Adding file search functionality
- Integrating with cloud storage services

## License

This project is open source and available under the MIT License.
