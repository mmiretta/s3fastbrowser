from datetime import datetime
from typing import Dict, List, Optional, Any
import json

class S3Object:
    """An S3 object."""
    
    def __init__(self, key: str, bucket: str, **kwargs):
        self.key = key
        self.bucket = bucket
        self.size = kwargs.get('size', 0)
        self.last_modified = kwargs.get('last_modified')
        self.etag = kwargs.get('etag', '')
        self.content_type = kwargs.get('content_type', '')
        self.metadata = kwargs.get('metadata', {})
        self.tags = kwargs.get('tags', {})
        self.versions = kwargs.get('versions', [])
        self.is_latest = kwargs.get('is_latest', True)
        self.version_id = kwargs.get('version_id')
        self.storage_class = kwargs.get('storage_class', 'STANDARD')
        self.encryption = kwargs.get('encryption', {})
        self.acl = kwargs.get('acl', {})
        
    @property
    def name(self) -> str:
        """File name (last part of the key)."""
        return self.key.split('/')[-1] if '/' in self.key else self.key
    
    @property
    def path(self) -> str:
        """Parent "folder" of the key (without the file name)."""
        return '/'.join(self.key.split('/')[:-1]) if '/' in self.key else ''
    
    @property
    def extension(self) -> str:
        """File extension, lower case, without the dot."""
        if '.' in self.name:
            return self.name.split('.')[-1].lower()
        return ''
    
    @property
    def is_folder(self) -> bool:
        """True for folder markers (keys ending in '/')."""
        return self.key.endswith('/')
    
    @property
    def size_formatted(self) -> str:
        """Human-readable size."""
        size = self.size
        if size == 0:
            return '0 B'
        
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size < 1024.0:
                return f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{size:.1f} PB"
    
    @property
    def last_modified_formatted(self) -> str:
        """Last-modified date as a string."""
        if not self.last_modified:
            return 'N/A'
        
        if isinstance(self.last_modified, str):
            try:
                self.last_modified = datetime.fromisoformat(self.last_modified.replace('Z', '+00:00'))
            except ValueError:
                return self.last_modified
        
        return self.last_modified.strftime('%Y-%m-%d %H:%M:%S')
    
    @property
    def icon_class(self) -> str:
        """Font Awesome icon class for the file type."""
        if self.is_folder:
            return 'fas fa-folder'
        
        ext = self.extension
        icon_map = {
            'pdf': 'fas fa-file-pdf',
            'doc': 'fas fa-file-word',
            'docx': 'fas fa-file-word',
            'xls': 'fas fa-file-excel',
            'xlsx': 'fas fa-file-excel',
            'ppt': 'fas fa-file-powerpoint',
            'pptx': 'fas fa-file-powerpoint',
            'txt': 'fas fa-file-alt',
            'json': 'fas fa-file-code',
            'xml': 'fas fa-file-code',
            'html': 'fas fa-file-code',
            'css': 'fas fa-file-code',
            'js': 'fas fa-file-code',
            'py': 'fas fa-file-code',
            'java': 'fas fa-file-code',
            'zip': 'fas fa-file-archive',
            'rar': 'fas fa-file-archive',
            '7z': 'fas fa-file-archive',
            'tar': 'fas fa-file-archive',
            'gz': 'fas fa-file-archive',
            'jpg': 'fas fa-file-image',
            'jpeg': 'fas fa-file-image',
            'png': 'fas fa-file-image',
            'gif': 'fas fa-file-image',
            'bmp': 'fas fa-file-image',
            'svg': 'fas fa-file-image',
            'webp': 'fas fa-file-image',
            'avif': 'fas fa-file-image',
            'ico': 'fas fa-file-image',
            'webm': 'fas fa-file-video',
            'mkv': 'fas fa-file-video',
            'm4v': 'fas fa-file-video',
            'ogg': 'fas fa-file-audio',
            'opus': 'fas fa-file-audio',
            'm4a': 'fas fa-file-audio',
            'aac': 'fas fa-file-audio',
            'csv': 'fas fa-file-csv',
            'tsv': 'fas fa-file-csv',
            'md': 'fas fa-file-lines',
            'log': 'fas fa-file-lines',
            'rtf': 'fas fa-file-word',
            'yaml': 'fas fa-file-code',
            'yml': 'fas fa-file-code',
            'sh': 'fas fa-terminal',
            'ts': 'fas fa-file-code',
            'go': 'fas fa-file-code',
            'rs': 'fas fa-file-code',
            'sql': 'fas fa-database',
            'mp4': 'fas fa-file-video',
            'avi': 'fas fa-file-video',
            'mov': 'fas fa-file-video',
            'mp3': 'fas fa-file-audio',
            'wav': 'fas fa-file-audio',
            'flac': 'fas fa-file-audio'
        }
        
        return icon_map.get(ext, 'fas fa-file')
    
    def to_dict(self) -> Dict[str, Any]:
        """Dictionary for JSON responses."""
        return {
            'key': self.key,
            'bucket': self.bucket,
            'name': self.name,
            'path': self.path,
            'size': self.size,
            'size_formatted': self.size_formatted,
            'last_modified': self.last_modified.isoformat() if self.last_modified else None,
            'last_modified_formatted': self.last_modified_formatted,
            'etag': self.etag,
            'content_type': self.content_type,
            'extension': self.extension,
            'is_folder': self.is_folder,
            'icon_class': self.icon_class,
            'metadata': self.metadata,
            'tags': self.tags,
            'versions': self.versions,
            'is_latest': self.is_latest,
            'version_id': self.version_id,
            'storage_class': self.storage_class,
            'encryption': self.encryption,
            'acl': self.acl
        }
    
    def to_json(self) -> str:
        """JSON string."""
        return json.dumps(self.to_dict(), default=str)
    
    @classmethod
    def from_s3_response(cls, s3_object: Dict[str, Any], bucket: str) -> 'S3Object':
        """Build from a boto3 list_objects_v2 entry."""
        return cls(
            key=s3_object.get('Key', ''),
            bucket=bucket,
            size=s3_object.get('Size', 0),
            last_modified=s3_object.get('LastModified'),
            etag=s3_object.get('ETag', '').strip('"'),
            content_type=s3_object.get('ContentType', ''),
            storage_class=s3_object.get('StorageClass', 'STANDARD')
        )
    
    def __str__(self) -> str:
        return f"S3Object(key='{self.key}', bucket='{self.bucket}', size={self.size})"
    
    def __repr__(self) -> str:
        return self.__str__()

class S3Bucket:
    """An S3 bucket."""
    
    def __init__(self, name: str, **kwargs):
        self.name = name
        self.creation_date = kwargs.get('creation_date')
        self.region = kwargs.get('region', 'us-east-1')
        self.versioning = kwargs.get('versioning', 'Disabled')
        self.encryption = kwargs.get('encryption', {})
        self.tags = kwargs.get('tags', {})
        self.objects_count = kwargs.get('objects_count', 0)
        self.total_size = kwargs.get('total_size', 0)
        
    def to_dict(self) -> Dict[str, Any]:
        """Dictionary for JSON responses."""
        return {
            'name': self.name,
            'creation_date': self.creation_date.isoformat() if self.creation_date else None,
            'region': self.region,
            'versioning': self.versioning,
            'encryption': self.encryption,
            'tags': self.tags,
            'objects_count': self.objects_count,
            'total_size': self.total_size
        } 