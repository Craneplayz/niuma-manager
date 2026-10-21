from copy import deepcopy
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / 'schemas/email-auth-v0.1.schema.json').read_text(encoding='utf-8'))
EXAMPLES = json.loads((ROOT / 'examples/email-auth-v01.json').read_text(encoding='utf-8'))


def validator(definition):
    # Select a root while retaining local references; no external registry needed.
    return Draft202012Validator({'$schema': SCHEMA['$schema'], '$defs': SCHEMA['$defs'],
                                '$ref': '#/$defs/' + definition})


class EmailAuthContractTests(unittest.TestCase):
    def test_schema_and_all_shared_examples_are_self_contained(self):
        Draft202012Validator.check_schema(SCHEMA)
        document = Draft202012Validator(SCHEMA)
        for definition, examples in EXAMPLES.items():
            for index, example in enumerate(examples):
                with self.subTest(definition=definition, index=index):
                    validator(definition).validate(example)
                    document.validate(example)
        for definition in ('emailCodeRegistrationRequest', 'emailCodeVerificationRequest',
                           'emailCodePasswordResetRequest'):
            for example in EXAMPLES[definition]:
                validator('emailCodeRequest').validate(example)
        for definition in ('emailError', 'legacyAuthError'):
            for example in EXAMPLES[definition]:
                validator('errorResponse').validate(example)

    def test_code_consuming_requests_require_a_six_digit_string_code(self):
        for definition in ('registerRequest', 'verifyEmailRequest', 'resetPasswordRequest'):
            original = EXAMPLES[definition][0]
            missing = deepcopy(original)
            missing.pop('code')
            self.assertFalse(validator(definition).is_valid(missing))
            for value in (123456, None, True, [], '12345', '1234567', '１２３４５６', '123456\n', 'abcdef'):
                with self.subTest(definition=definition, value=value):
                    self.assertFalse(validator(definition).is_valid(dict(original, code=value)))
            validator(definition).validate(dict(original, code='000001'))

    def test_wrong_or_missing_purpose_and_registration_email_are_rejected(self):
        for request in ({}, {'purpose': 'password_reset', 'email': 'student@example.test'},
                        {'purpose': 'REGISTER', 'email': 'student@example.test'},
                        {'purpose': ['register'], 'email': 'student@example.test'},
                        {'purpose': 'register'}, {'purpose': 'register', 'email': None},
                        {'purpose': 'register', 'email': 'missing-at-sign'},
                        {'purpose': 'reset_password'}, {'purpose': 'reset_password', 'email': None},
                        {'purpose': 'reset_password', 'email': 'missing-at-sign'}):
            with self.subTest(request=request):
                self.assertFalse(validator('emailCodeRequest').is_valid(request))

    def test_ignored_request_extensions_match_the_http_boundary(self):
        for definition in ('emailCodeRegistrationRequest', 'emailCodeVerificationRequest',
                           'emailCodePasswordResetRequest', 'registerRequest', 'verifyEmailRequest',
                           'resetPasswordRequest'):
            original = EXAMPLES[definition][0]
            validator(definition).validate(dict(original, ignored={'arbitrary': ['extension']}))
        # Both authenticated endpoints obtain their mailbox from the session.
        # Even an invalid extra email must not imply a rejected HTTP request.
        for definition in ('emailCodeVerificationRequest', 'verifyEmailRequest'):
            for email in (None, [], {'ignored': True}, 'not-an-email'):
                validator(definition).validate(dict(EXAMPLES[definition][0], email=email))

    def test_invalid_known_request_fields_are_rejected(self):
        original = EXAMPLES['registerRequest'][0]
        for key, value in [('name', ''), ('name', '   '), ('name', 'a' * 41),
                           ('email', []), ('password', 'short'), ('password', 'a' * 129),
                           ('challengeId', None), ('challengeId', ''), ('challengeId', 'a' * 101)]:
            with self.subTest(field=key):
                self.assertFalse(validator('registerRequest').is_valid(dict(original, **{key: value})))

    def test_password_reset_requires_its_email_challenge_and_bounded_new_password(self):
        original = EXAMPLES['resetPasswordRequest'][0]
        for key in ('email', 'challengeId', 'code', 'newPassword'):
            with self.subTest(missing=key):
                missing = deepcopy(original)
                missing.pop(key)
                self.assertFalse(validator('resetPasswordRequest').is_valid(missing))
        for key, value in [('email', None), ('email', 'invalid'), ('email', []),
                           ('challengeId', None), ('challengeId', ''), ('challengeId', 'a' * 101),
                           ('newPassword', 'short'), ('newPassword', 'a' * 129),
                           ('newPassword', 12345678), ('newPassword', None)]:
            with self.subTest(field=key, value=value):
                self.assertFalse(validator('resetPasswordRequest').is_valid(dict(original, **{key: value})))
        for size in (8, 128):
            validator('resetPasswordRequest').validate(dict(original, newPassword='a' * size))

    def test_password_reset_success_cannot_expose_private_data_or_log_in(self):
        original = EXAMPLES['resetPasswordResponse'][0]
        self.assertFalse(validator('resetPasswordResponse').is_valid({}))
        for value in (False, 1, 'true', None):
            self.assertFalse(validator('resetPasswordResponse').is_valid({'reset': value}))
        for key in ('user', 'password', 'newPassword', 'code', 'token', 'session', 'smtpResponse'):
            with self.subTest(field=key):
                self.assertFalse(validator('resetPasswordResponse').is_valid(
                    dict(original, **{key: 'synthetic-private-value'})))

    def test_challenge_response_does_not_expose_the_code_or_provider_details(self):
        original = EXAMPLES['challengeResponse'][0]
        for key, value in [('code', '012345'), ('password', 'synthetic'), ('smtpResponse', 'synthetic'),
                           ('expiresIn', 0), ('expiresIn', '600'), ('retryAfter', 0), ('challengeId', None)]:
            with self.subTest(field=key):
                self.assertFalse(validator('challengeResponse').is_valid(dict(original, **{key: value})))

    def test_public_user_requires_boolean_verification_and_excludes_credentials(self):
        original = EXAMPLES['publicUser'][0]
        for value in ('true', 1, None):
            self.assertFalse(validator('publicUser').is_valid(dict(original, emailVerified=value)))
        missing = deepcopy(original)
        missing.pop('emailVerified')
        self.assertFalse(validator('publicUser').is_valid(missing))
        for key in ('salt', 'hash', 'password', 'token'):
            self.assertFalse(validator('publicUser').is_valid(dict(original, **{key: 'synthetic-private-value'})))
        for avatar in (None, 'data:image/png;base64,aGVsbG8='):
            self.assertTrue(validator('publicUser').is_valid(dict(original, avatar=avatar)))
        for avatar in (True, 1, {}, 'https://example.test/avatar.png', 'data:image/svg+xml;base64,aGVsbG8='):
            self.assertFalse(validator('publicUser').is_valid(dict(original, avatar=avatar)))
        legacy = EXAMPLES['userResponse'][0]
        validator('userResponse').validate(legacy)
        self.assertFalse(validator('verifiedUserResponse').is_valid(legacy))

    def test_error_codes_retry_after_and_legacy_errors_are_distinct(self):
        rate = next(item for item in EXAMPLES['emailError'] if item['code'] == 'email_code_rate_limited')
        missing = deepcopy(rate)
        missing.pop('retryAfter')
        self.assertFalse(validator('emailError').is_valid(missing))
        for value in (0, -1, '60', True, None):
            self.assertFalse(validator('emailError').is_valid(dict(rate, retryAfter=value)))
        normal = EXAMPLES['emailError'][0]
        for change in ({'retryAfter': 60}, {'code': 'unknown'}, {'error': None},
                       {'smtpResponse': 'synthetic-private-value'}):
            self.assertFalse(validator('errorResponse').is_valid(dict(normal, **change)))
        validator('errorResponse').validate({'error': '登录已失效，请重新登录'})
        self.assertFalse(validator('emailError').is_valid({'error': '登录已失效，请重新登录'}))


if __name__ == '__main__':
    unittest.main()
