import os
import uuid
from datetime import datetime
from flask import Flask, request, render_template, send_file, redirect, url_for, flash, jsonify
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.utils import secure_filename
import mimetypes
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__, static_folder='static')
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'your-secret-key-change-this')

# Configuration
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = set()  # Allow all file types
MAX_CONTENT_LENGTH = 100 * 1024 * 1024  # 100MB max file size

# Authentication credentials from environment variables
ADMIN_USERNAME = os.getenv('ADMIN_USERNAME', 'admin')
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'password123')

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH

# Create uploads directory if it doesn't exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Initialize Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Please log in to access this page.'

# Simple User class for authentication
class User(UserMixin):
    def __init__(self, id):
        self.id = id

@login_manager.user_loader
def load_user(user_id):
    return User(user_id)

# Custom filter for timestamp conversion
@app.template_filter('timestamp_to_date')
def timestamp_to_date(timestamp):
    return datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')

def allowed_file(filename):
    """Check if file extension is allowed"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS or len(ALLOWED_EXTENSIONS) == 0

def get_file_info(filepath):
    """Get file information"""
    if os.path.exists(filepath):
        stat = os.stat(filepath)
        filename = os.path.basename(filepath)
        file_ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
        
        # Define image extensions
        image_extensions = {'jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'svg', 'ico', 'tiff', 'tif'}
        is_image = file_ext in image_extensions
        
        return {
            'name': filename,
            'size': stat.st_size,
            'modified': stat.st_mtime,
            'extension': file_ext,
            'is_image': is_image
        }
    return None

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Login page"""
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            user = User(username)
            login_user(user)
            flash('Successfully logged in!')
            return redirect(url_for('index'))
        else:
            flash('Invalid username or password')
    
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    """Logout user"""
    logout_user()
    flash('You have been logged out')
    return redirect(url_for('index'))

@app.route('/')
@app.route('/<path:folder_path>')
def index(folder_path=''):
    """Main page showing files and folders"""
    files = []
    folders = []
    current_path = folder_path
    full_path = os.path.join(UPLOAD_FOLDER, current_path)
    
    if os.path.exists(full_path):
        for item in os.listdir(full_path):
            item_path = os.path.join(full_path, item)
            if os.path.isfile(item_path):
                file_info = get_file_info(item_path)
                if file_info:
                    file_info['relative_path'] = os.path.join(current_path, item).replace('\\', '/')
                    files.append(file_info)
            elif os.path.isdir(item_path):
                stat = os.stat(item_path)
                folders.append({
                    'name': item,
                    'modified': stat.st_mtime,
                    'path': os.path.join(current_path, item).replace('\\', '/')
                })
    
    # Sort files and folders by modification time (newest first)
    files.sort(key=lambda x: x['modified'], reverse=True)
    folders.sort(key=lambda x: x['modified'], reverse=True)
    
    return render_template('index.html', files=files, folders=folders, current_path=current_path)

@app.route('/create_folder', methods=['POST'])
@login_required
def create_folder():
    """Create a new folder"""
    folder_name = request.form.get('folder_name', '').strip()
    current_path = request.form.get('current_path', '')
    
    if not folder_name:
        flash('Folder name cannot be empty')
        return redirect(url_for('index', folder_path=current_path))
    
    # Sanitize folder name
    folder_name = secure_filename(folder_name)
    if not folder_name:
        flash('Invalid folder name')
        return redirect(url_for('index', folder_path=current_path))
    
    full_path = os.path.join(UPLOAD_FOLDER, current_path, folder_name)
    
    if os.path.exists(full_path):
        flash(f'Folder "{folder_name}" already exists')
        return redirect(url_for('index', folder_path=current_path))
    
    try:
        os.makedirs(full_path, exist_ok=True)
        flash(f'Folder "{folder_name}" created successfully!')
    except Exception as e:
        flash(f'Error creating folder: {str(e)}')
    
    return redirect(url_for('index', folder_path=current_path))

@app.route('/upload', methods=['POST'])
@login_required
def upload_file():
    """Handle file upload"""
    if 'file' not in request.files:
        flash('No file selected')
        return redirect(request.url)
    
    file = request.files['file']
    current_path = request.form.get('current_path', '')
    
    if file.filename == '':
        flash('No file selected')
        return redirect(url_for('index', folder_path=current_path))
    
    if file:
        # Use the original filename
        filename = secure_filename(file.filename)
        if not filename:
            # If filename is empty after securing, generate one
            filename = f"file_{uuid.uuid4().hex[:8]}"
        
        # Create upload directory path
        upload_dir = os.path.join(app.config['UPLOAD_FOLDER'], current_path)
        os.makedirs(upload_dir, exist_ok=True)
        
        filepath = os.path.join(upload_dir, filename)
        
        # If file already exists, add a number suffix
        counter = 1
        original_filename = filename
        while os.path.exists(filepath):
            name, ext = os.path.splitext(original_filename)
            filename = f"{name}_{counter}{ext}"
            filepath = os.path.join(upload_dir, filename)
            counter += 1
        
        file.save(filepath)
        flash(f'File "{filename}" uploaded successfully!')
    
    return redirect(url_for('index', folder_path=current_path))

@app.route('/download/<path:file_path>')
def download_file(file_path):
    """Download a file"""
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], file_path)
    if os.path.exists(filepath) and os.path.isfile(filepath):
        return send_file(filepath, as_attachment=True)
    else:
        flash('File not found')
        return redirect(url_for('index'))

@app.route('/view/<path:file_path>')
def view_file(file_path):
    """View a file directly in browser (for images, PDFs, etc.)"""
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], file_path)
    if os.path.exists(filepath) and os.path.isfile(filepath):
        return send_file(filepath)
    else:
        flash('File not found')
        return redirect(url_for('index'))

@app.route('/delete/<path:file_path>', methods=['POST'])
@login_required
def delete_file(file_path):
    """Delete a file or folder"""
    full_path = os.path.join(app.config['UPLOAD_FOLDER'], file_path)
    current_path = request.form.get('current_path', '')
    
    if os.path.exists(full_path):
        try:
            if os.path.isfile(full_path):
                os.remove(full_path)
                flash(f'File "{os.path.basename(file_path)}" deleted successfully!')
            elif os.path.isdir(full_path):
                # Check if folder is empty
                if os.listdir(full_path):
                    flash(f'Folder "{os.path.basename(file_path)}" is not empty and cannot be deleted')
                    return redirect(url_for('index', folder_path=current_path))
                os.rmdir(full_path)
                flash(f'Folder "{os.path.basename(file_path)}" deleted successfully!')
        except Exception as e:
            flash(f'Error deleting "{os.path.basename(file_path)}": {str(e)}')
    else:
        flash('File or folder not found')
    
    return redirect(url_for('index', folder_path=current_path))

@app.route('/rename/<path:item_path>', methods=['POST'])
@login_required
def rename_item(item_path):
    """Rename a file or folder"""
    new_name = request.form.get('new_name', '').strip()
    current_path = request.form.get('current_path', '')
    
    if not new_name:
        flash('New name cannot be empty')
        return redirect(url_for('index', folder_path=current_path))
    
    # Sanitize new name
    new_name = secure_filename(new_name)
    if not new_name:
        flash('Invalid name')
        return redirect(url_for('index', folder_path=current_path))
    
    old_full_path = os.path.join(app.config['UPLOAD_FOLDER'], item_path)
    new_full_path = os.path.join(os.path.dirname(old_full_path), new_name)
    
    if not os.path.exists(old_full_path):
        flash('Item not found')
        return redirect(url_for('index', folder_path=current_path))
    
    if os.path.exists(new_full_path):
        flash(f'"{new_name}" already exists')
        return redirect(url_for('index', folder_path=current_path))
    
    try:
        os.rename(old_full_path, new_full_path)
        flash(f'Renamed successfully to "{new_name}"')
    except Exception as e:
        flash(f'Error renaming: {str(e)}')
    
    return redirect(url_for('index', folder_path=current_path))

@app.route('/api/files')
def api_files():
    """API endpoint to get list of files"""
    files = []
    if os.path.exists(UPLOAD_FOLDER):
        for filename in os.listdir(UPLOAD_FOLDER):
            filepath = os.path.join(UPLOAD_FOLDER, filename)
            if os.path.isfile(filepath):
                file_info = get_file_info(filepath)
                if file_info:
                    files.append(file_info)
    
    files.sort(key=lambda x: x['modified'], reverse=True)
    return jsonify(files)

if __name__ == '__main__':
    port = int(os.getenv('PORT', 80))
    app.run(host='0.0.0.0', port=port, debug=False)
