"""Private Blob storage with bounded chunks and HTTP byte-range delivery."""
import io
import re
from pathlib import PurePath

from PIL import Image, UnidentifiedImageError
from vercel.blob import sync as blob
from werkzeug.utils import secure_filename

CHUNK_SIZE = 3 * 1024 * 1024
FORM_LIMIT = CHUNK_SIZE + 1024
FILE_TYPES = {
    '.pdf':'application/pdf', '.xlsx':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    '.xls':'application/vnd.ms-excel', '.csv':'text/csv', '.pptx':'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    '.ppt':'application/vnd.ms-powerpoint', '.docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.doc':'application/msword', '.txt':'text/plain', '.png':'image/png', '.jpg':'image/jpeg', '.jpeg':'image/jpeg',
    '.webp':'image/webp', '.gif':'image/gif', '.mp4':'video/mp4', '.webm':'video/webm', '.mov':'video/quicktime',
    '.m4v':'video/mp4', '.mp3':'audio/mpeg', '.wav':'audio/wav', '.ogg':'audio/ogg', '.m4a':'audio/mp4',
    '.aac':'audio/aac', '.flac':'audio/flac',
}
Image.MAX_IMAGE_PIXELS = 20_000_000


class PrivateBlobStorage:
    def put(self, pathname, data):
        return blob.put_blob(pathname, data, access='private', content_type='application/octet-stream',
                             allow_overwrite=True).pathname

    def get(self, pathname):
        return blob.get_blob(pathname, access='private').body


def validate_upload(filename, size, purpose):
    filename = secure_filename(filename)
    extension = PurePath(filename).suffix.lower()
    mime = FILE_TYPES.get(extension)
    if not filename or len(filename)>180 or not mime:
        raise ValueError('Choose a supported document, image, audio or video file.')
    if purpose not in ('lesson','branding'):
        raise ValueError('Choose lesson material or branding image.')
    if purpose=='branding' and mime not in ('image/png','image/jpeg','image/webp'):
        raise ValueError('Branding images must be PNG, JPG or WebP.')
    maximum = (250 if mime.startswith(('audio/','video/')) else 10 if mime.startswith('image/') else 50) * 1024 * 1024
    if not 0<size<=maximum:
        raise ValueError(f'This file type allows up to {maximum//1024//1024} MB; empty files are not supported.')
    return filename,mime


def validate_image(data, mime):
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.width*image.height>20_000_000 or Image.MIME.get(image.format)!=mime:
                raise ValueError('The image format or dimensions are not supported.')
            image.verify()
    except (UnidentifiedImageError,OSError,SyntaxError,Image.DecompressionBombError) as exc:
        raise ValueError('Upload a valid image file (up to 20 million pixels).') from exc


def byte_range(value, size):
    if not value:
        return 0,size-1,200
    match=re.fullmatch(r'bytes=(\d*)-(\d*)',value)
    if not match or not any(match.groups()):
        raise ValueError('Unsupported byte range.')
    left,right=match.groups()
    if not left:
        suffix=int(right)
        if suffix<=0: raise ValueError('Invalid range.')
        return max(0,size-suffix),size-1,206
    start=int(left)
    end=min(int(right),size-1) if right else size-1
    if start>=size or end<start: raise ValueError('Range outside file.')
    return start,end,206
