"""Verification mail via Alibaba Cloud DirectMail; no SDK or secret logging."""
import os
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid


MAIL_KEYS = frozenset({'NIUMA_MAIL_HOST', 'NIUMA_MAIL_PORT', 'NIUMA_MAIL_FROM',
                       'NIUMA_MAIL_PASSWORD', 'NIUMA_MAIL_TIMEOUT'})
# Use the region selected in the DirectMail console, regardless of the ECS region.
# https://help.aliyun.com/zh/direct-mail/smtp-endpoints
SMTP_HOSTS = frozenset({'smtpdm.aliyun.com', 'smtpdm-ap-southeast-1.aliyuncs.com',
                        'smtpdm-us-east-1.aliyuncs.com', 'smtpdm-eu-central-1.aliyuncs.com'})
PURPOSES = {'register': '注册账号', 'verify_email': '验证邮箱', 'verify': '验证邮箱',
            'reset_password': '重置密码'}
UNAVAILABLE = '邮件服务暂不可用，请稍后重试或联系管理员'
NOT_CONFIGURED = '邮件服务尚未配置，请联系管理员'
_LABEL = r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?'
_ADDRESS = re.compile(r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]+@(?:" + _LABEL + r'\.)+' + _LABEL, re.ASCII)


class DeliveryError(Exception):
    def __init__(self, message=UNAVAILABLE, code='email_send_failed'):
        self.message = message
        self.code = code
        super().__init__(message)


def _address(value):
    return (isinstance(value, str) and len(value) <= 254
            and _ADDRESS.fullmatch(value) is not None
            and '..' not in value and not value.startswith('.') and '.@' not in value
            and len(value.split('@', 1)[0]) <= 64)


def _configuration(values):
    sender, password = values.get('NIUMA_MAIL_FROM', ''), values.get('NIUMA_MAIL_PASSWORD', '')
    if not sender or not password:
        raise DeliveryError(NOT_CONFIGURED, code='email_not_configured')
    host = values.get('NIUMA_MAIL_HOST') or 'smtpdm.aliyun.com'
    try:
        port = int(values.get('NIUMA_MAIL_PORT') or '465')
        timeout = float(values.get('NIUMA_MAIL_TIMEOUT') or '10')
    except (ValueError, TypeError):
        raise DeliveryError() from None
    if (host not in SMTP_HOSTS or port != 465 or not 3 <= timeout <= 30
            or not _address(sender) or not isinstance(password, str)
            or any(ord(char) < 32 or ord(char) == 127 for char in password)):
        raise DeliveryError()
    return host, port, sender, password, timeout


def configured(values=None):
    """A safe readiness flag; never connects or exposes the settings."""
    try:
        _configuration(os.environ if values is None else values)
    except DeliveryError:
        return False
    return True


def send_verification(email, code, purpose):
    """Send one code to one address. Errors never contain SMTP responses/secrets."""
    host, port, sender, password, timeout = _configuration(os.environ)
    if (not _address(email) or not isinstance(code, str)
            or re.fullmatch(r'[0-9]{6}', code) is None
            or not isinstance(purpose, str) or purpose not in PURPOSES):
        raise DeliveryError()
    try:
        message = EmailMessage()
        message['From'] = formataddr(('牛马管理器', sender))
        message['To'] = email
        message['Subject'] = '牛马管理器 · ' + PURPOSES[purpose] + '验证码'
        message['Date'] = formatdate(localtime=False, usegmt=True)
        message['Message-ID'] = make_msgid(domain=sender.rsplit('@', 1)[1])
        message.set_content('你正在' + PURPOSES[purpose] + '。\n\n验证码：' + code
                            + '\n\n验证码 10 分钟内有效，请勿向他人透露此验证码。'
                            + '\n如果这不是你的操作，请忽略此邮件。\n')
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, timeout=timeout, context=context) as client:
            client.login(sender, password)
            refused = client.send_message(message, from_addr=sender, to_addrs=[email])
            if refused:
                raise DeliveryError()
    except (OSError, smtplib.SMTPException, ValueError, UnicodeError):
        # Provider errors may include the sender, recipient, message or credentials.
        # In particular, never enable smtplib debuglevel or forward its response.
        raise DeliveryError() from None
