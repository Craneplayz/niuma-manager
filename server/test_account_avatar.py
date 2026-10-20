"""Real HTTP avatar persistence, account boundaries, and image validation."""
import base64
import io
import json
import tempfile
import threading
import unittest
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

from PIL import Image, PngImagePlugin

import app
import account_avatar


class AccountAvatarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.previous_db = app.DB
        cls.tmp = tempfile.TemporaryDirectory()
        app.DB = Path(cls.tmp.name) / 'avatars.sqlite3'
        app.initialize(recover=False)
        cls.server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = 'http://127.0.0.1:' + str(cls.server.server_port) + '/api'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        app.DB = cls.previous_db
        cls.tmp.cleanup()

    def client(self):
        return build_opener(HTTPCookieProcessor(CookieJar()))

    def call(self, client, path, method='GET', data=None):
        try:
            response = client.open(Request(self.base + path, None if data is None else json.dumps(data).encode(), {'Content-Type': 'application/json'}, method=method))
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.loads(response.read())

    def register(self, client, suffix):
        email = self._testMethodName + suffix + '@test.local'
        status, result = self.call(client, '/auth/register', 'POST', dict(name='Test', email=email, password='Test-password-2026'))
        self.assertEqual(status, 200)
        self.assertIsNone(result['user']['avatar'])
        return result['user']

    @staticmethod
    def image(size=(128, 128), color='red', image_format='PNG', **options):
        output = io.BytesIO()
        Image.new('RGB', size, color).save(output, format=image_format, **options)
        return account_avatar.PREFIX + base64.b64encode(output.getvalue()).decode('ascii')

    def upload(self, client, value):
        status, result = self.call(client, '/auth/avatar', 'POST', dict(avatar=value))
        self.assertEqual(status, 200)
        return result['user']['avatar']

    def test_normalization_persistence_login_and_profile_preserve_avatar(self):
        client = self.client()
        user = self.register(client, '')
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text('private-note', 'must not survive normalization')
        source = self.image(size=(64, 96), pnginfo=metadata)
        saved = self.upload(client, source)
        with Image.open(io.BytesIO(base64.b64decode(saved.split(',', 1)[1]))) as image:
            self.assertEqual(image.format, 'PNG')
            self.assertEqual(image.size, (256, 256))
            self.assertEqual(image.getpixel((0, 0)), (255, 0, 0, 255))
            self.assertEqual(image.info, {})
        self.assertNotEqual(saved, source)
        with app.connection() as db:
            self.assertEqual(db.execute('SELECT data_url FROM user_avatars WHERE user_id=?', (user['id'],)).fetchone()[0], saved)
        app.initialize(recover=False)
        self.assertEqual(self.call(client, '/auth/me')[1]['user']['avatar'], saved)
        status, result = self.call(client, '/auth/profile', 'POST', dict(name='Updated'))
        self.assertEqual(status, 200)
        self.assertEqual(result['user']['avatar'], saved)
        self.assertEqual(result['user']['name'], 'Updated')
        self.assertEqual(self.call(client, '/auth/logout', 'POST', {})[0], 200)
        fresh = self.client()
        status, result = self.call(fresh, '/auth/login', 'POST', dict(email=user['email'], password='Test-password-2026'))
        self.assertEqual(status, 200)
        self.assertEqual(result['user']['avatar'], saved)

    def test_account_isolation_and_restore_default(self):
        owner, other = self.client(), self.client()
        own_user = self.register(owner, '-owner')
        other_user = self.register(other, '-other')
        own_avatar = self.upload(owner, self.image())
        self.assertIsNone(self.call(other, '/auth/me')[1]['user']['avatar'])
        other_avatar = self.upload(other, self.image(color='blue'))
        self.assertEqual(self.call(other, '/auth/avatar', 'POST', dict(avatar=None, user_id=own_user['id']))[0], 400)
        self.assertEqual(self.call(owner, '/auth/me')[1]['user']['avatar'], own_avatar)
        self.assertIsNone(self.upload(owner, None))
        self.assertIsNone(self.call(owner, '/auth/me')[1]['user']['avatar'])
        self.assertEqual(self.call(other, '/auth/me')[1]['user']['avatar'], other_avatar)
        with app.connection() as db:
            self.assertIsNone(db.execute('SELECT 1 FROM user_avatars WHERE user_id=?', (own_user['id'],)).fetchone())
            self.assertIsNotNone(db.execute('SELECT 1 FROM user_avatars WHERE user_id=?', (other_user['id'],)).fetchone())
        self.assertIsNone(self.upload(owner, None))

    def test_invalid_or_oversized_images_leave_previous_avatar_intact(self):
        client = self.client()
        user = self.register(client, '')
        saved = self.upload(client, self.image())
        valid = self.image()
        png = base64.b64decode(valid.split(',', 1)[1])
        damaged = bytearray(png)
        damaged[-8] ^= 1
        animation = io.BytesIO()
        Image.new('RGB', (16, 16), 'red').save(animation, format='PNG', save_all=True, append_images=[Image.new('RGB', (16, 16), 'blue')], duration=100)
        cases = [
            ({}, 400),
            (dict(avatar=True), 400),
            (dict(avatar={}), 400),
            (dict(avatar=[]), 400),
            (dict(avatar=''), 400),
            (dict(avatar=valid, user_id='someone-else'), 400),
            (dict(avatar=account_avatar.PREFIX + '???'), 400),
            (dict(avatar=account_avatar.PREFIX + base64.b64encode(b'<svg/>').decode()), 400),
            (dict(avatar=self.image(image_format='JPEG')), 400),
            (dict(avatar=valid.replace('image/png', 'image/jpeg')), 400),
            (dict(avatar=account_avatar.PREFIX + base64.b64encode(png[:50]).decode()), 400),
            (dict(avatar=account_avatar.PREFIX + base64.b64encode(damaged).decode()), 400),
            (dict(avatar=account_avatar.PREFIX + base64.b64encode(animation.getvalue()).decode()), 400),
            (dict(avatar=self.image(size=(513, 256))), 413),
            (dict(avatar=self.image(size=(256, 513))), 413),
            (dict(avatar=account_avatar.PREFIX + 'A' * account_avatar.MAX_DATA_URL_LENGTH), 413),
        ]
        for body, expected in cases:
            with self.subTest(expected=expected, value_type=type(body.get('avatar')).__name__, length=len(body.get('avatar', '')) if isinstance(body.get('avatar'), str) else None):
                self.assertEqual(self.call(client, '/auth/avatar', 'POST', body)[0], expected)
                self.assertEqual(self.call(client, '/auth/me')[1]['user']['avatar'], saved)

    def test_avatar_requires_an_authenticated_session(self):
        anonymous = self.client()
        for avatar in (self.image(), None):
            self.assertEqual(self.call(anonymous, '/auth/avatar', 'POST', dict(avatar=avatar))[0], 401)
        client = self.client()
        user = self.register(client, '')
        saved = self.upload(client, self.image())
        self.assertEqual(self.call(client, '/auth/logout', 'POST', {})[0], 200)
        self.assertEqual(self.call(client, '/auth/avatar', 'POST', dict(avatar=None))[0], 401)
        with app.connection() as db:
            self.assertEqual(db.execute('SELECT data_url FROM user_avatars WHERE user_id=?', (user['id'],)).fetchone()[0], saved)


if __name__ == '__main__':
    unittest.main()
