// 業務連絡表（明細行）の入力・自動計算の共通コア。
// 講師の報告書一覧（tutor/reports.html: PC明細行・スマホ詳細シート）と
// 事務ダッシュボードの報告書修正（office/queue.html）で共用する唯一のルール定義。
// 入力仕様（自動計算・種別・労基休憩・時限）の変更は必ずこのファイルで行い、片側だけの修正は禁止。
// 純粋関数のみ（DOM・ページ状態に依存しない）。列定義(columns)・コマ設定(slots)は呼び出し側が渡す。
(function () {
  'use strict';

  // ===== 定数（report_view・PDF・サーバ判定と共有する不変キーを含む） =====
  // 勤怠区分（種別）: 空＝勤務（既定）。有給休暇・欠勤の行は勤務時間を持たず、月内の取得回数・欠勤回数として集計する。
  // 自己都合・学校行事の行は担当時限・担当業務（分）を持たない（0分固定）が、副業務等はその日の実績を手動入力できる。
  const ATTENDANCE_KINDS = [
    {value: '', label: '勤務'},
    {value: 'paid_leave', label: '有給'},
    {value: 'absent', label: '欠勤'},
    {value: 'personal_reason', label: '自己都合'},
    {value: 'school_event', label: '学校行事'}
  ];
  // 担当業務（分）の列キー（契約の動的列 task_minutes_N とデフォルト列 teach_minutes）
  const MAIN_DUTY_FIELD_RE = /^(task_minutes_\d+|teach_minutes)$/;
  // 行の背景色: 勤務（無色）／有給（amber）／欠勤（rose）／自己都合（violet）／学校行事（sky）
  const KIND_ROW_BG = {paid_leave: 'bg-amber-50', absent: 'bg-rose-50', personal_reason: 'bg-violet-50', school_event: 'bg-sky-50'};
  const CIRCLED_NUMBERS = '①②③④⑤⑥⑦⑧⑨⑩';
  const WORK_START_TIME = '08:40';           // 業務開始時間（コマ設定が無い契約の固定値・手動入力不可）
  const MINUTES_PER_PERIOD = 50;             // 担当時限1コマあたりの担当業務（分）（コマ設定が無い契約）
  const BREAK_MINUTES_PER_EXTRA_PERIOD = 10; // 休憩時間（分）＝（コマ数−1）×10（コマ設定が無い契約）
  const DAY_END_MINUTES = 23 * 60 + 59;      // 終了時間は同日23:59まで（超過は計算不可として保存をブロック）
  const WEEKDAYS = ['日', '月', '火', '水', '木', '金', '土'];

  function isLeaveKind(value) { return value === 'paid_leave' || value === 'absent'; }
  // 自己都合・学校行事: 担当時限＝選択不可、担当業務（分）＝0固定。その他（副業務・採点・休憩・交通費）は手動入力可。
  function isNoMainDutyKind(value) { return value === 'personal_reason' || value === 'school_event'; }
  function numberValue(value) {
    const n = Number(value || 0);
    return Number.isFinite(n) ? n : 0;
  }
  function timeToMinutes(value) {
    if (!value) return null;
    const [hours, minutes] = value.split(':').map(Number);
    return hours * 60 + minutes;
  }
  function minutesToTime(total) {
    const normalized = ((total % 1440) + 1440) % 1440;
    return `${String(Math.floor(normalized / 60)).padStart(2, '0')}:${String(normalized % 60).padStart(2, '0')}`;
  }
  function weekdayLabel(value) {
    if (!value) return '';
    const d = new Date(`${value}T00:00:00`);
    return Number.isNaN(d.getTime()) ? '' : WEEKDAYS[d.getDay()];
  }
  // 日付入力ボックス内の右側に併記する曜日「(月)」（曜日以外はすべて半角）
  function weekdayParen(value) {
    const wd = weekdayLabel(value);
    return wd ? `(${wd})` : '';
  }
  // エラーメッセージ用の「M月D日（曜）」表記
  function dateLabelJa(value) {
    const d = new Date(`${value}T00:00:00`);
    if (Number.isNaN(d.getTime())) return String(value || '');
    return `${d.getMonth() + 1}月${d.getDate()}日（${WEEKDAYS[d.getDay()]}）`;
  }

  // 担当時限：複数選択（1〜10）。値は「1・3・6」の文字列で保持（report_view/PDF/既存単一値と互換）。
  function parseSubjectPeriods(value) {
    return String(value || '').split('・').map(s => s.trim()).filter(s => s !== '');
  }

  // ===== 列定義からのキー導出 =====
  // 業務開始〜終了時間の合計対象＝担当時限より右の「分」列（担当業務・副担当業務・採点の分・休憩時間）。
  // 往復交通費（円）と採点の回数は時間ではないため除外する。
  function timeMinuteKeys(columns) {
    const periodIndex = columns.findIndex(column => column.key === 'subject_period');
    const keys = [];
    columns.slice(periodIndex + 1).forEach(column => {
      if (column.type === 'count_minutes') keys.push(column.minutes_key);
      else if (column.type === 'number' && column.key !== 'commute_fee') keys.push(column.key);
    });
    return keys;
  }
  // 担当時限（コマ数）の自動入力先＝右隣の担当業務（分）列。休憩・交通費は自動入力先にしない。
  function periodAutoFillKey(columns) {
    const periodIndex = columns.findIndex(column => column.key === 'subject_period');
    const next = periodIndex === -1 ? null : columns[periodIndex + 1];
    if (!next) return null;
    if (next.type === 'count_minutes') return next.minutes_key;
    if (next.type === 'number' && next.key !== 'break_minutes' && next.key !== 'commute_fee') return next.key;
    return null;
  }
  function mainMinuteKeys(columns) {
    return timeMinuteKeys(columns).filter(key => MAIN_DUTY_FIELD_RE.test(key));
  }
  // 副担当業務等（担当業務・休憩以外の分列＝副業務・採点の分）: コマ間の隙間を消費する時間
  function secondaryMinuteKeys(columns) {
    return timeMinuteKeys(columns).filter(key => key !== 'break_minutes' && !MAIN_DUTY_FIELD_RE.test(key));
  }
  function lineMinutesTotal(line, keys) {
    return keys.reduce((sum, key) => sum + numberValue(line[key]), 0);
  }

  // ===== 業務開始〜終了時間の自動計算 =====
  // 分数合計から業務開始〜終了時間を求める共通計算。開始は WORK_START_TIME 固定
  // （コマ設定契約は選択コマの開始を startMinutesOverride で指定）。
  // 合計0分は未計算（空）、同日23:59超過は overflow=true（計算不可）。
  function computeAutoTimes(total, startMinutesOverride = null) {
    const startMinutes = startMinutesOverride ?? timeToMinutes(WORK_START_TIME);
    const overflow = total > 0 && startMinutes + total > DAY_END_MINUTES;
    const start = (total > 0 && !overflow) ? minutesToTime(startMinutes) : '';
    const end = start ? minutesToTime(startMinutes + total) : '';
    return {start, end, overflow};
  }
  // 行の業務開始（分）: コマ設定契約は選択した最初のコマの開始（時限未選択の行はコマ①の開始）、
  // コマ設定が無い契約は null（＝WORK_START_TIME 固定）を返す。
  function rowStartMinutesOverride(slots, subjectPeriod, kind) {
    if (!slots || isLeaveKind(kind)) return null;
    return slotSelectionMetrics(slots, subjectPeriod).startMinutes;
  }
  // 担当時限のコマ数から自動入力する 担当業務（分）・休憩時間（分）（コマ設定が無い契約）。
  function periodAutoFillValues(count) {
    return {
      task: count > 0 ? String(count * MINUTES_PER_PERIOD) : '',
      breakMinutes: count > 0 ? String((count - 1) * BREAK_MINUTES_PER_EXTRA_PERIOD) : ''
    };
  }
  // 担当時限の時間帯「08:40〜09:30」。開始8:40から（50分授業＋10分休憩）の繰り返しで導出する（コマ設定が無い契約）。
  function periodTimeRange(n) {
    const start = timeToMinutes(WORK_START_TIME) + (n - 1) * (MINUTES_PER_PERIOD + BREAK_MINUTES_PER_EXTRA_PERIOD);
    return `${minutesToTime(start)}〜${minutesToTime(start + MINUTES_PER_PERIOD)}`;
  }

  // ===== 契約の前期/後期（期別設定）の解決 =====
  // 契約の workload_cases（[{task_index:1|2, start_date, end_date, slots:[{start,end}...], ...}]）から
  // 「入力タイミング＝今日」を基準に適用中の期を1つだけ解決する（task_index 1=前期 / 2=後期）。
  // 過去月の報告書（差戻し編集・事務修正）は今日を対象月内へクランプした日（＝月末時点の期）で解決する。
  // 旧形式（適用期間なしのケース・契約単位の period_slots）は従来どおりのフォールバックで互換を保つ。
  function termLabel(taskIndex) {
    return Number(taskIndex) === 1 ? '【前期】' : Number(taskIndex) === 2 ? '【後期】' : '';
  }
  // 期別設定を task_index 正規化＋適用開始日順で返す（旧データの task_index 無しは前期扱い）
  function contractTermCases(contract) {
    return (Array.isArray(contract?.workload_cases) ? contract.workload_cases : [])
      .filter(c => c && typeof c === 'object')
      .map(c => ({...c, task_index: Number(c.task_index) || 1}))
      .sort((a, b) => String(a.start_date || '').localeCompare(String(b.start_date || '')) || a.task_index - b.task_index);
  }
  function caseContainsDate(c, dateStr) {
    return (!c.start_date || c.start_date <= dateStr) && (!c.end_date || dateStr <= c.end_date);
  }
  function caseOverlapsMonth(c, month) {
    if (!month) return true;
    // YYYY-MM-DD の文字列比較で判定（"-31"は月末超えでも比較上は月内日付を包含できる）
    return (!c.start_date || c.start_date <= `${month}-31`) && (!c.end_date || c.end_date >= `${month}-01`);
  }
  // 端末ローカルの今日（YYYY-MM-DD）
  function localTodayIso() {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  }
  // 期判定の基準日＝入力タイミング（今日）。対象月外の月（過去月の差戻し編集など）は月内へクランプする。
  function termReferenceDate(month, todayStr) {
    const today = todayStr || localTodayIso();
    if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(String(month || ''))) return today;
    const [y, m] = String(month).split('-').map(Number);
    const monthStart = `${month}-01`;
    const monthEnd = `${month}-${String(new Date(y, m, 0).getDate()).padStart(2, '0')}`;
    return today < monthStart ? monthStart : (today > monthEnd ? monthEnd : today);
  }
  // 対象月の報告書に適用する期ケースを1つだけ解決する（該当なしは null）。
  // 基準日を含む期 → 対象月と重なる期（開始日順）の順。適用期間を持つ新形式のケースのみが対象で、
  // 期間なしの旧ケースしか無い契約は null（列・コマ設定とも従来動作へフォールバック）。
  function activeTermCaseForMonth(contract, month, todayStr) {
    const cases = contractTermCases(contract).filter(c => c.start_date && c.end_date);
    if (!cases.length) return null;
    const ref = termReferenceDate(month, todayStr);
    return cases.find(c => caseContainsDate(c, ref))
      || cases.find(c => caseOverlapsMonth(c, month))
      || null;
  }
  // 対象月の報告書に適用するコマ設定（適用期の slots → 旧形式の契約単位 period_slots の順）。無ければ null。
  function termSlotsForMonth(contract, month, todayStr) {
    if (!contract) return null;
    const activeCase = activeTermCaseForMonth(contract, month, todayStr);
    if (activeCase && Array.isArray(activeCase.slots) && activeCase.slots.length) return activeCase.slots;
    const legacy = contract.period_slots;
    return (Array.isArray(legacy) && legacy.length) ? legacy : null;
  }
  // 担当時限の自動入力先。適用期に対応する task_minutes_{task_index} 列があれば優先する
  // （2列時代のスナップショットを持つ報告書でも正しい期の列へ入力するため）。無ければ右隣ルール。
  function periodAutoFillKeyForCase(columns, activeCase) {
    if (activeCase && activeCase.task_index) {
      const key = `task_minutes_${activeCase.task_index}`;
      if (columns.some(column => column.key === key)) return key;
    }
    return periodAutoFillKey(columns);
  }

  // ===== コマ設定（契約の時間割）による自動計算 =====
  // 「08:30」→「8:30」（表示用に時の先頭0を省く）
  function slotTimeLabel(value) { return String(value || '').replace(/^0/, ''); }
  function slotRangeLabel(slot) { return `${slotTimeLabel(slot.start)}〜${slotTimeLabel(slot.end)}`; }
  // スケジュール欄への自動反映テキスト「① 8:30〜9:20、② 9:30〜10:20、…」
  function slotScheduleText(slots) {
    return slots.map((slot, index) => `${CIRCLED_NUMBERS[index]} ${slotRangeLabel(slot)}`).join('、');
  }
  function slotDuration(slot) { return Math.max(0, timeToMinutes(slot.end) - timeToMinutes(slot.start)); }
  // 選択中の時限（"2・4"）のうちコマ設定の範囲内のものを昇順の数値配列で返す
  function selectedSlotNumbers(slots, subjectPeriod) {
    return parseSubjectPeriods(subjectPeriod).map(Number)
      .filter(n => Number.isInteger(n) && n >= 1 && n <= slots.length)
      .sort((a, b) => a - b);
  }
  // コマ選択から導く自動値。startMinutes＝時刻がいちばん早い選択コマの開始（未選択の行は全コマ中の最早開始）、
  // taskMinutes＝選択コマの実時間の合計、gapMinutes＝選択コマ間の隙間（休憩・副担当業務へ割当可能）。
  // コマ番号は時間順とは限らない（例: ⑤が①より早い朝コマ）ため、選択コマを開始時刻順に並べ替えて計算する。
  function slotSelectionMetrics(slots, subjectPeriod) {
    const picked = selectedSlotNumbers(slots, subjectPeriod);
    if (!picked.length) {
      return {selected: false, startMinutes: Math.min(...slots.map(slot => timeToMinutes(slot.start))), taskMinutes: 0, gapMinutes: 0};
    }
    const ordered = picked.map(n => slots[n - 1]).sort((a, b) => timeToMinutes(a.start) - timeToMinutes(b.start));
    let taskMinutes = 0;
    let gapMinutes = 0;
    ordered.forEach((slot, index) => {
      taskMinutes += slotDuration(slot);
      if (index > 0) gapMinutes += Math.max(0, timeToMinutes(slot.start) - timeToMinutes(ordered[index - 1].end));
    });
    return {selected: true, startMinutes: timeToMinutes(ordered[0].start), taskMinutes, gapMinutes};
  }
  // 労基法の休憩下限: 実働（休憩を除く担当＋副担当等の合計）が6時間超→45分以上・8時間超→60分以上
  function requiredBreakMinutes(workMinutes) {
    if (workMinutes > 480) return 60;
    if (workMinutes > 360) return 45;
    return 0;
  }
  // 休憩時間（分）の自動計算コア。
  // recompute=true（担当時限・副担当業務の変更時）はコマ間の隙間から副担当合計を除いた残りで上書きし、
  // 常に労基下限を下回らないよう引き上げる（手動修正は次の変更まで保持される）。
  // 戻り値 {value: 書き込む休憩の分（null=変更なし）, required: 下限へ引き上げた分数（0=引き上げなし）}
  function slotBreakDecision(slots, line, recompute, columns) {
    if (!slots || isLeaveKind(line.kind)) return {value: null, required: 0};
    const secondary = secondaryMinuteKeys(columns).reduce((sum, key) => sum + numberValue(line[key]), 0);
    const work = secondary + mainMinuteKeys(columns).reduce((sum, key) => sum + numberValue(line[key]), 0);
    const required = requiredBreakMinutes(work);
    const metrics = slotSelectionMetrics(slots, line.subject_period);
    let value = null;
    if (recompute && metrics.selected) value = Math.max(metrics.gapMinutes - secondary, 0);
    const current = value != null ? value : numberValue(line.break_minutes);
    if (current < required) return {value: required, required};
    return {value, required: 0};
  }
  function breakBumpMessage(required) {
    return `実働が${required >= 60 ? 8 : 6}時間を超えるため、休憩時間（分）を${required}分に自動調整しました`;
  }

  // ===== 保存時の検証補助 =====
  function findDuplicateLineDate(lines) {
    const seen = new Set();
    for (const line of lines) {
      const value = String(line.date || '').trim();
      if (!value) continue;
      if (seen.has(value)) return value;
      seen.add(value);
    }
    return null;
  }
  // 記入があるのに日付が未入力の明細行の位置（0はじまり。無ければ-1）。
  // 提出前ガードで使用する（サーバ側 api/reports._assert_no_undated_lines と同一ルール）。
  function findUndatedLineIndex(lines) {
    return (lines || []).findIndex(line => line && typeof line === 'object'
      && !String(line.date || '').trim()
      && Object.entries(line).some(([key, value]) => key !== 'date' && String(value ?? '').trim() !== ''));
  }

  // ===== 入力ルールのヒント文言（担当時限・業務開始〜終了時間） =====
  function subjectPeriodHintText(slots) {
    if (!slots) return '1〜10から担当した時限を選択します。選択したコマ数×50分が右隣の担当業務（分）へ、（コマ数−1）×10分が休憩時間（分）へ自動入力されます。自動入力後の分数は1分単位で修正できます（担当時限を選び直すと自動値で上書きされます）。';
    return `契約管理のコマ設定（①〜${CIRCLED_NUMBERS[slots.length - 1]}）から担当した時限を選択します。業務開始は選択したコマのうち時刻がいちばん早いコマの開始時刻、担当業務（分）は選択コマの合計時間、休憩時間（分）はコマ間の隙間から副担当業務等の分を除いた時間が自動入力されます（分は1分単位で修正できます。時限を選び直すと自動値で上書きされます）。`;
  }
  function timeRangeHintText(slots) {
    if (!slots) return '業務開始〜終了時間は自動計算のため手動入力できません。開始は8:40固定、終了は担当時限より右の時間（分）列（担当業務・副担当業務・採点の分・休憩時間）の合計を開始時間に加算した時刻です。往復交通費（円）・採点の回数は加算しません。';
    return '業務開始〜終了時間は自動計算のため手動入力できません。開始は選択したコマのうち時刻がいちばん早いコマの開始時刻（時限未選択の行は時間割の最早開始）、終了は開始に担当業務・副担当業務・採点の分・休憩時間の合計を加算した時刻です。実働（休憩を除く）が6時間を超える場合は45分以上、8時間を超える場合は60分以上の休憩が必要です（不足時は自動調整されます）。';
  }

  window.WorkReportCalc = {
    ATTENDANCE_KINDS, MAIN_DUTY_FIELD_RE, KIND_ROW_BG, CIRCLED_NUMBERS,
    WORK_START_TIME, MINUTES_PER_PERIOD, BREAK_MINUTES_PER_EXTRA_PERIOD, DAY_END_MINUTES,
    isLeaveKind, isNoMainDutyKind, numberValue, timeToMinutes, minutesToTime,
    weekdayParen, dateLabelJa, parseSubjectPeriods,
    timeMinuteKeys, periodAutoFillKey, mainMinuteKeys, secondaryMinuteKeys, lineMinutesTotal,
    termLabel, contractTermCases, localTodayIso, activeTermCaseForMonth, termSlotsForMonth, periodAutoFillKeyForCase,
    computeAutoTimes, rowStartMinutesOverride, periodAutoFillValues, periodTimeRange,
    slotTimeLabel, slotRangeLabel, slotScheduleText, slotDuration, selectedSlotNumbers, slotSelectionMetrics,
    requiredBreakMinutes, slotBreakDecision, breakBumpMessage,
    findDuplicateLineDate, findUndatedLineIndex, subjectPeriodHintText, timeRangeHintText
  };
})();
