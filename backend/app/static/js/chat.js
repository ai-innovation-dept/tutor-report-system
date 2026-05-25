// # === Phase 6: アプリ内チャット START ===
async function pollMessages(reportId, render) {
  let after = null;
  setInterval(async () => {
    const items = await api(`/api/reports/${reportId}/messages${after ? `?after_id=${after}` : ''}`);
    if (items.length) {
      after = items[items.length - 1].id;
      render(items);
    }
  }, 5000);
}
// # === Phase 6 END ===

