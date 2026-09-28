import getpass
import uuid

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction

from accounts.models import AdminAccount, AuditLog


class Command(BaseCommand):
    help = "Interactively create the one owner account; no password is accepted as a CLI argument."

    def handle(self, *args, **options):
        if AdminAccount.objects.filter(kind=AdminAccount.Kind.OWNER).exists():
            raise CommandError("主账号已存在；没有创建新账号。")
        login_name = input("主账号登录名：").strip().lower()
        display_name = input("主账号显示名：").strip()
        password = getpass.getpass("主账号密码：")
        repeated = getpass.getpass("再次输入密码：")
        if not login_name or not display_name or password != repeated:
            raise CommandError("输入不完整或两次密码不一致。")
        try:
            validate_password(password)
        except ValidationError as exc:
            raise CommandError("密码强度不足：" + " ".join(exc.messages)) from exc
        try:
            with transaction.atomic():
                owner = AdminAccount.objects.create_user(login_name, password, display_name=display_name,
                                                         kind=AdminAccount.Kind.OWNER)
                AuditLog.objects.create(actor=owner, action_code="account.bootstrap",
                    object_type="admin_account", object_id=str(owner.id), before={},
                    after={"kind": "OWNER"}, result="SUCCESS", request_id=uuid.uuid4())
        except IntegrityError as exc:
            raise CommandError("主账号或登录名已存在；没有创建新账号。") from exc
        self.stdout.write(self.style.SUCCESS("主账号已创建。请将凭据按项目约定交接，命令不会显示密码。"))
