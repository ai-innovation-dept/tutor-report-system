# === 指導時間の計算規約（サーバ側の唯一の定義源） START ===
"""指導時間の計算規約。

- 在室時間 ＝ 終了時刻 − 開始時刻 − 休憩等の時間（1分単位・負値は0に丸め）
- 指導時間数 ＝ 在室時間を15分単位で切り捨てた時間
  例）1時間14分→1時間00分／1時間15分→1時間15分／1時間16分→1時間15分／
      1時間29分→1時間15分／1時間30分→1時間30分

指導時間数は開始・終了時刻そのものを丸めるのではなく、算出された在室時間に対して
15分単位の切り捨てを行う。一覧・参照画面・確認票PDF・通知メール・月報の自動反映など、
指導時間数を扱う箇所はすべて本モジュールを経由すること。

フロント側の複製は backend/app/static/js/lesson_time.js（同一ルール）。
変更時は必ず両方を同時に更新すること。
"""

TEACHING_UNIT_MINUTES = 15


def presence_minutes(report) -> int:
    """在室時間（分）＝ 終了時刻 − 開始時刻 − 休憩等の時間。"""
    start = report.start_time.hour * 60 + report.start_time.minute
    end = report.end_time.hour * 60 + report.end_time.minute
    return max(0, end - start - (report.break_minutes or 0))


def teaching_minutes(report) -> int:
    """指導時間数（分）＝ 在室時間を15分単位で切り捨て。"""
    return presence_minutes(report) // TEACHING_UNIT_MINUTES * TEACHING_UNIT_MINUTES


def duration_label(minutes: int) -> str:
    """「N時間M分」表示（例: 90→「1時間30分」・60→「1時間」・45→「45分」）。"""
    hours, mins = divmod(minutes, 60)
    if hours and mins:
        return f"{hours}時間{mins}分"
    if hours:
        return f"{hours}時間"
    return f"{mins}分"
# === 指導時間の計算規約（サーバ側の唯一の定義源） END ===
