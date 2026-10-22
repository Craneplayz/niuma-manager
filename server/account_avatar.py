"""Account-owned PNG avatars stored in the current environment's SQLite database."""
import base64
import binascii
import io
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError


PREFIX = 'data:image/png;base64,'
MAX_DATA_URL_LENGTH = 512 * 1024
MAX_DIMENSION = 512
AVATAR_DIMENSION = 256


def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS user_avatars(user_id TEXT PRIMARY KEY, data_url TEXT NOT NULL)')


def read(db, user_id):
    row = db.execute('SELECT data_url FROM user_avatars WHERE user_id=?', (user_id,)).fetchone()
    return row[0] if row else None


def normalize(value, ApiError):
    if not isinstance(value, str) or not value.startswith(PREFIX):
        raise ApiError(400, '请选择 PNG 格式的头像')
    if len(value) > MAX_DATA_URL_LENGTH:
        raise ApiError(413, '头像图片过大，请重新选择')
    try:
        encoded = base64.b64decode(value[len(PREFIX):], validate=True)
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(encoded)) as image:
                if image.format != 'PNG':
                    raise ApiError(400, '请选择 PNG 格式的头像')
                if max(image.size) > MAX_DIMENSION:
                    raise ApiError(413, '头像尺寸过大，请重新选择')
                if getattr(image, 'is_animated', False):
                    raise ApiError(400, '请选择静态头像图片')
                image.verify()
            with Image.open(io.BytesIO(encoded)) as image:
                cropped = ImageOps.fit(image.convert('RGBA'), (AVATAR_DIMENSION, AVATAR_DIMENSION), method=Image.Resampling.LANCZOS)
                # A fresh image discards source metadata, including EXIF and text chunks.
                clean = Image.new('RGBA', cropped.size)
                clean.paste(cropped)
                output = io.BytesIO()
                clean.save(output, format='PNG')
    except (ValueError, binascii.Error, OSError, SyntaxError, UnidentifiedImageError,
            Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ApiError(400, '头像图片无法读取，请重新选择') from None
    return PREFIX + base64.b64encode(output.getvalue()).decode('ascii')


def save(db, user_id, body, ApiError):
    if set(body) != {'avatar'}:
        raise ApiError(400, '头像请求只能包含 avatar')
    value = body['avatar']
    if value is None:
        db.execute('DELETE FROM user_avatars WHERE user_id=?', (user_id,))
        return None
    normalized = normalize(value, ApiError)
    db.execute('INSERT INTO user_avatars(user_id,data_url) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET data_url=excluded.data_url', (user_id, normalized))
    return normalized
