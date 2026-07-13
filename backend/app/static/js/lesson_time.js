// === 指導時間の計算規約（フロント側の唯一の定義源） START ===
// - 在室時間 ＝ 終了時刻 − 開始時刻 − 休憩等の時間（1分単位・負値は0に丸め）
// - 指導時間数 ＝ 在室時間を15分単位で切り捨てた時間
//   例）1時間14分→1時間00分／1時間15分→1時間15分／1時間29分→1時間15分／1時間30分→1時間30分
// 指導時間数は開始・終了時刻そのものを丸めるのではなく、算出された在室時間に対して切り捨てる。
// サーバ側の複製は backend/app/services/lesson_time.py（同一ルール）。変更時は必ず両方を同時に更新すること。
(function () {
  const TEACHING_UNIT_MINUTES = 15;

  function timeText(value) {
    return (value || '').slice(0, 5);
  }

  // "HH:MM" 同士の差（分）。未入力・逆転は0。
  function minutesBetween(start, end) {
    if (!start || !end) return 0;
    const [sh, sm] = start.split(':').map(Number);
    const [eh, em] = end.split(':').map(Number);
    return Math.max(0, (eh * 60 + em) - (sh * 60 + sm));
  }

  // 在室時間（分）を開始/終了("HH:MM")・休憩（分）から算出
  function presenceMinutesOf(start, end, breakMinutes) {
    return Math.max(0, minutesBetween(timeText(start), timeText(end)) - Number(breakMinutes || 0));
  }

  // 在室時間（分）を報告書オブジェクト（start_time/end_time/break_minutes）から算出
  function presenceMinutes(report) {
    return presenceMinutesOf(report.start_time, report.end_time, report.break_minutes);
  }

  // 指導時間数（分）＝ 在室時間を15分単位で切り捨て
  function floorTeachingMinutes(presence) {
    return Math.floor(Math.max(0, presence) / TEACHING_UNIT_MINUTES) * TEACHING_UNIT_MINUTES;
  }

  function teachingMinutes(report) {
    return floorTeachingMinutes(presenceMinutes(report));
  }

  // 「N時間M分」表示（例: 90→「1時間30分」・60→「1時間」・45→「45分」）
  function durationLabel(minutes) {
    const hours = Math.floor(minutes / 60);
    const mins = minutes % 60;
    if (hours && mins) return `${hours}時間${mins}分`;
    if (hours) return `${hours}時間`;
    return `${mins}分`;
  }

  window.LessonTime = {
    TEACHING_UNIT_MINUTES,
    minutesBetween,
    presenceMinutesOf,
    presenceMinutes,
    floorTeachingMinutes,
    teachingMinutes,
    durationLabel,
  };
})();
// === 指導時間の計算規約（フロント側の唯一の定義源） END ===
