"""SMTP設定の疎通確認スクリプト。現在の .env のSMTP設定で実際にテストメールを1通送る。

送信経路（ホスト/ポート/認証/TLS）は両システム共通のため、これで両系の配信可否を確認できる
（送信元アドレスのみ legacy=SMTP_FROM / new=NEW_SMTP_FROM で別。本スクリプトは legacy 設定で送信）。

使い方:
    docker compose exec backend python -m app.scripts.send_test_email <宛先メールアドレス>
例:
    docker compose exec backend python -m app.scripts.send_test_email kintaikanri.tutor1@gmail.com
"""
import sys

from app.config import settings
from app.services.mailer import _send_via_smtp


def main(argv=None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("使い方: python -m app.scripts.send_test_email <宛先メールアドレス>")
        return
    to = argv[0]
    print("現在のSMTP設定:")
    print(
        f"  host={settings.smtp_host} port={settings.smtp_port} tls={settings.smtp_tls} "
        f"auth={'あり('+settings.smtp_username+')' if settings.smtp_username else 'なし'} "
        f"from={settings.smtp_from}"
    )
    if settings.smtp_host in {"mailhog", "localhost", ""}:
        print(
            "⚠️ 送信先が MailHog/localhost です。実際のメール配信には外部SMTPサービスの設定（.env の"
            " SMTP_HOST/PORT/USERNAME/PASSWORD/SMTP_TLS と SMTP_FROM/NEW_SMTP_FROM）が必要です。"
        )
    try:
        # 疎通確認のため送信キューを介さず即時送信する（MAIL_BACKEND に関わらず実SMTP送信）。
        _send_via_smtp(
            to,
            "【テスト】SMTP送信確認",
            "これはSMTP設定の疎通確認用テストメールです。\nこのメールが届けば送信設定は正常です。",
        )
        print(f"OK: {to} への送信処理が完了しました（SMTPサーバーが受理）。受信箱/迷惑メールを確認してください。")
    except Exception as exc:  # noqa: BLE001 - 失敗内容を表示するのが目的
        print(f"NG: 送信に失敗しました: {type(exc).__name__}: {exc}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
