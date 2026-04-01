from __future__ import annotations

import html
import json


def render_directory_page(title: str, request_path: str, upload_panel: str) -> str:
    page = """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>__TITLE__</title>
<style>
:root {
  --bg-top: #f7fafc;
  --bg-bottom: #eef3f8;
  --card-bg: #ffffff;
  --card-border: #d8e2ee;
  --text-main: #1a2433;
  --text-muted: #617185;
  --primary: #2f6fed;
  --primary-strong: #2458c4;
  --accent-soft: #e9f0ff;
  --danger: #c12f3a;
  --line: #e4ebf3;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  min-height: 100vh;
  color: var(--text-main);
  font-family: "Segoe UI", "Helvetica Neue", "Noto Sans", sans-serif;
  background: linear-gradient(180deg, var(--bg-top), var(--bg-bottom));
}
.app-shell {
  width: min(1040px, calc(100vw - 32px));
  margin: 20px auto 28px;
}
.app-header {
  background: var(--card-bg);
  border: 1px solid var(--card-border);
  border-radius: 14px;
  padding: 18px 20px;
  box-shadow: 0 8px 24px rgba(20, 48, 90, 0.06);
}
.app-header h1 {
  margin: 0;
  font-size: 26px;
  letter-spacing: 0.2px;
}
.path-note {
  margin: 8px 0 0;
  color: var(--text-muted);
  font-size: 14px;
  word-break: break-all;
}
.path-note code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  background: var(--accent-soft);
  border-radius: 8px;
  padding: 2px 8px;
  color: #21488a;
}
.section-gap { height: 12px; }
.list-card {
  background: var(--card-bg);
  border: 1px solid var(--card-border);
  border-radius: 14px;
  overflow: hidden;
  box-shadow: 0 8px 24px rgba(20, 48, 90, 0.05);
}
.list-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  font-size: 13px;
  color: var(--text-muted);
  border-bottom: 1px solid var(--line);
}
.table-scroll {
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
}
table {
  border-collapse: collapse;
  width: 100%;
  min-width: 660px;
}
th, td {
  text-align: left;
  padding: 10px 14px;
  border-bottom: 1px solid var(--line);
}
th {
  background: #f6f9fd;
  color: #5a6c82;
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.4px;
}
tbody tr:hover {
  background: #f8fbff;
}
.name-col { min-width: 220px; width: auto; }
.size-col { width: 130px; color: #42556b; white-space: nowrap; }
.time-col { width: 190px; color: #5f7186; white-space: nowrap; }
.action-col { width: 120px; white-space: nowrap; text-align: right; }
.item-link {
  display: inline-block;
  white-space: nowrap;
  color: var(--text-main);
  text-decoration: none;
}
.item-link:hover {
  color: #1d4fae;
  text-decoration: underline;
}
.empty-row {
  color: #5f7186;
}
.upload-card {
  background: var(--card-bg);
  border: 1px solid var(--card-border);
  border-radius: 14px;
  padding: 14px;
  box-shadow: 0 8px 24px rgba(20, 48, 90, 0.05);
}
.upload-top {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.visually-hidden-input {
  position: absolute;
  width: 1px;
  height: 1px;
  margin: -1px;
  padding: 0;
  overflow: hidden;
  clip: rect(0 0 0 0);
  border: 0;
}
.picked-file-name {
  min-width: 220px;
  max-width: min(52vw, 520px);
  padding: 8px 10px;
  border: 1px solid #d3deeb;
  border-radius: 10px;
  background: #f8fbff;
  color: #4a5e75;
  font-size: 13px;
  white-space: nowrap;
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
}
.upload-meta {
  margin-top: 10px;
  display: flex;
  justify-content: space-between;
  gap: 10px;
  color: var(--text-muted);
  font-size: 13px;
  flex-wrap: wrap;
}
.upload-status {
  min-height: 20px;
  margin-top: 8px;
  font-size: 14px;
}
.btn {
  appearance: none;
  border: 1px solid transparent;
  border-radius: 10px;
  padding: 8px 14px;
  font-size: 14px;
  line-height: 1.2;
  cursor: pointer;
  transition: background .2s ease, color .2s ease, border-color .2s ease, transform .06s ease;
}
.btn:active {
  transform: translateY(1px);
}
.btn:disabled {
  cursor: not-allowed;
  opacity: 0.6;
  transform: none;
}
.btn-primary {
  background: var(--primary);
  color: #fff;
  border-color: var(--primary);
}
.btn-primary:hover {
  background: var(--primary-strong);
  border-color: var(--primary-strong);
}
.btn-secondary {
  background: #fff;
  color: #315689;
  border-color: #b9cbea;
}
.btn-secondary:hover {
  background: #f3f7ff;
}
.btn-danger {
  background: #fff;
  color: #b3343f;
  border-color: #e0b5ba;
}
.btn-danger:hover {
  background: #fff3f4;
  border-color: #cf8990;
}
.btn-ghost {
  background: #fff;
  color: #5a6880;
  border-color: #ccd7e8;
}
.btn-ghost:hover {
  background: #f7faff;
}
.progress-wrap {
  margin-top: 9px;
  height: 10px;
  border-radius: 999px;
  background: #edf2fa;
  border: 1px solid #d6e0ee;
  overflow: hidden;
}
.progress-bar {
  height: 100%;
  width: 0%;
  border-radius: inherit;
  background: linear-gradient(90deg, #2f6fed, #4787ff);
  transition: width .18s ease;
}
.progress-line {
  margin-top: 6px;
  color: #5e7188;
  font-size: 12px;
}
.btn-small {
  padding: 6px 10px;
  font-size: 12px;
  border-radius: 8px;
}
.hidden {
  display: none !important;
}
.modal {
  position: fixed;
  inset: 0;
  z-index: 9999;
}
.modal-backdrop {
  position: absolute;
  inset: 0;
  background: rgba(16, 31, 56, 0.45);
}
.modal-panel {
  position: relative;
  margin: min(12vh, 100px) auto 0;
  width: min(420px, calc(100vw - 26px));
  background: #fff;
  border: 1px solid #d7e1ef;
  border-radius: 14px;
  padding: 16px;
  box-shadow: 0 24px 48px rgba(13, 31, 59, 0.25);
}
.modal-panel h3 {
  margin: 0 0 8px;
  font-size: 19px;
}
.modal-panel p {
  margin: 0;
  white-space: pre-line;
  color: #4c6078;
  font-size: 14px;
  line-height: 1.5;
}
.modal-actions {
  margin-top: 16px;
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  flex-wrap: wrap;
}
@media (max-width: 760px) {
  .app-shell { width: calc(100vw - 18px); margin: 10px auto 18px; }
  .app-header { padding: 14px; border-radius: 12px; }
  .app-header h1 { font-size: 22px; }
  th, td { padding: 9px 8px; font-size: 13px; }
  .list-head { padding: 10px 10px; }
  .upload-card { padding: 12px; border-radius: 12px; }
  .upload-top > .btn { width: 100%; }
  .picked-file-name { min-width: 100%; max-width: 100%; }
  .upload-top { align-items: stretch; }
  .modal-actions .btn { width: auto; }
}
</style>
</head>
<body>
<main class="app-shell">
<section class="app-header">
  <h1>__TITLE__</h1>
  <p class="path-note">Current path: <code>__PATH_LABEL__</code></p>
</section>
<div class="section-gap"></div>
__UPLOAD_PANEL__
<div class="section-gap"></div>
<section class="list-card">
  <div class="list-head"><span>Items</span><span id="item-count">Loading...</span></div>
  <div class="table-scroll">
  <table>
  <thead><tr><th class="name-col">Name</th><th class="size-col">Size</th><th class="time-col">Modified</th><th class="action-col">Action</th></tr></thead>
  <tbody id="file-table-body">
  <tr>
    <td class="name-col empty-row">Loading...</td>
    <td class="size-col">-</td>
    <td class="time-col">-</td>
    <td class="action-col">-</td>
  </tr>
  </tbody>
  </table>
  </div>
</section>
</main>
<script>
(function() {
  const currentDir = __CURRENT_DIR__;
  const tableBodyEl = document.getElementById('file-table-body');
  const itemCountEl = document.getElementById('item-count');

  function escapeHtml(value) {
    return String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function setListError(message) {
    const text = message || 'Failed to load file list.';
    itemCountEl.textContent = '0 entries';
    tableBodyEl.innerHTML = '' +
      '<tr>' +
      '<td class="name-col empty-row">' + escapeHtml(text) + '</td>' +
      '<td class="size-col">-</td>' +
      '<td class="time-col">-</td>' +
      '<td class="action-col">-</td>' +
      '</tr>';
  }

  function renderEntries(entries) {
    if (!Array.isArray(entries) || entries.length === 0) {
      itemCountEl.textContent = '0 entries';
      tableBodyEl.innerHTML = '' +
        '<tr>' +
        '<td class="name-col empty-row">Empty directory</td>' +
        '<td class="size-col">-</td>' +
        '<td class="time-col">-</td>' +
        '<td class="action-col">-</td>' +
        '</tr>';
      return;
    }

    itemCountEl.textContent = String(entries.length) + ' entries';
    const parts = [];
    for (const item of entries) {
      let actionHtml = '-';
      if (item && item.can_delete && !item.is_parent && item.name) {
        actionHtml =
          '<button class="btn btn-danger btn-small delete-btn" type="button" data-delete-name="' +
          escapeHtml(item.name) +
          '">Delete</button>';
      }
      parts.push(
        '<tr>' +
          '<td class="name-col"><a class="item-link" href="' + escapeHtml(item.href || '#') + '">' +
            escapeHtml(item.display_name || '') +
          '</a></td>' +
          '<td class="size-col">' + escapeHtml(item.size || '-') + '</td>' +
          '<td class="time-col">' + escapeHtml(item.modified || '-') + '</td>' +
          '<td class="action-col">' + actionHtml + '</td>' +
        '</tr>'
      );
    }
    tableBodyEl.innerHTML = parts.join('');
  }

  async function loadFileList() {
    try {
      const query = new URLSearchParams({ directory: currentDir });
      const response = await fetch('/.api/list?' + query.toString(), { cache: 'no-store' });
      let data = {};
      try {
        data = await response.json();
      } catch (e) {
        data = { message: response.statusText || 'Request failed' };
      }
      if (!response.ok) {
        throw new Error(data.message || ('HTTP ' + response.status));
      }
      renderEntries(data.entries || []);
      return data;
    } catch (err) {
      setListError(err && err.message ? err.message : String(err));
      throw err;
    }
  }

  window.reloadFileList = loadFileList;
  loadFileList().catch(function() {
    return null;
  });
})();
</script>
</body>
</html>
"""
    return (
        page.replace("__TITLE__", html.escape(title))
        .replace("__PATH_LABEL__", html.escape(request_path))
        .replace("__UPLOAD_PANEL__", upload_panel)
        .replace("__CURRENT_DIR__", json.dumps(request_path))
    )


def render_upload_panel(request_path: str, chunk_size: int) -> str:
    template = """
<section class="upload-card">
  <div class="upload-top">
    <input id="upload-file" class="visually-hidden-input" type="file" />
    <button id="pick-file-btn" class="btn btn-secondary" type="button">Choose File</button>
    <div id="picked-file-name" class="picked-file-name">No file selected</div>
    <button id="upload-btn" class="btn btn-primary" type="button">Upload File</button>
  </div>
  <div class="upload-meta">
    <span>Chunk size: __CHUNK_SIZE__ bytes</span>
    <span id="upload-detail">Ready</span>
  </div>
  <div id="upload-status" class="upload-status">Idle.</div>
  <div class="progress-wrap"><div id="upload-progress-bar" class="progress-bar"></div></div>
  <div class="progress-line"><span id="upload-progress-text">0%</span></div>
</section>

<div id="ui-modal" class="modal hidden" aria-hidden="true">
  <div id="modal-backdrop" class="modal-backdrop"></div>
  <div class="modal-panel" role="dialog" aria-modal="true" aria-labelledby="modal-title">
    <h3 id="modal-title">Confirm Action</h3>
    <p id="modal-message"></p>
    <div class="modal-actions">
      <button id="modal-dismiss" class="btn btn-ghost" type="button">Cancel</button>
      <button id="modal-secondary" class="btn btn-secondary hidden" type="button">Secondary</button>
      <button id="modal-primary" class="btn btn-primary" type="button">OK</button>
    </div>
  </div>
</div>

<script>
(function() {
  const currentDir = __CURRENT_DIR__;
  const chunkSize = __CHUNK_SIZE__;
  const fileInput = document.getElementById('upload-file');
  const pickFileBtn = document.getElementById('pick-file-btn');
  const pickedFileNameEl = document.getElementById('picked-file-name');
  const uploadBtn = document.getElementById('upload-btn');
  const detailEl = document.getElementById('upload-detail');
  const statusEl = document.getElementById('upload-status');
  const progressBarEl = document.getElementById('upload-progress-bar');
  const progressTextEl = document.getElementById('upload-progress-text');

  const modalEl = document.getElementById('ui-modal');
  const modalBackdropEl = document.getElementById('modal-backdrop');
  const modalTitleEl = document.getElementById('modal-title');
  const modalMessageEl = document.getElementById('modal-message');
  const modalPrimaryEl = document.getElementById('modal-primary');
  const modalSecondaryEl = document.getElementById('modal-secondary');
  const modalDismissEl = document.getElementById('modal-dismiss');
  let modalResolver = null;

  function setStatus(text, isError) {
    statusEl.textContent = text;
    statusEl.style.color = isError ? '#c12f3a' : '#233549';
  }

  function setDetail(text) {
    detailEl.textContent = text;
  }

  function setWriteControlsDisabled(disabled) {
    uploadBtn.disabled = disabled;
    pickFileBtn.disabled = disabled;
    const buttons = Array.prototype.slice.call(document.querySelectorAll('.delete-btn'));
    buttons.forEach(function(btn) {
      btn.disabled = disabled;
    });
  }

  function setPickedFileLabel(file) {
    if (!file) {
      pickedFileNameEl.textContent = 'No file selected';
      return;
    }
    pickedFileNameEl.textContent = file.name + ' (' + formatBytes(file.size) + ')';
  }

  function formatBytes(value) {
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let size = Number(value) || 0;
    let index = 0;
    while (size >= 1024 && index < units.length - 1) {
      size = size / 1024;
      index += 1;
    }
    if (index === 0) {
      return String(Math.floor(size)) + ' ' + units[index];
    }
    return size.toFixed(1) + ' ' + units[index];
  }

  function setProgress(done, total) {
    if (total <= 0) {
      progressBarEl.style.width = '0%';
      progressTextEl.textContent = '0%';
      return;
    }
    const percent = Math.max(0, Math.min(100, Math.floor(done / total * 100)));
    progressBarEl.style.width = String(percent) + '%';
    progressTextEl.textContent = String(percent) + '%';
  }

  function closeModal(result) {
    if (!modalResolver) return;
    const resolver = modalResolver;
    modalResolver = null;
    modalEl.classList.add('hidden');
    modalEl.setAttribute('aria-hidden', 'true');
    resolver(result);
  }

  function showChoiceModal(options) {
    if (modalResolver) {
      closeModal('dismiss');
    }
    modalTitleEl.textContent = options.title || 'Confirm Action';
    modalMessageEl.textContent = options.message || '';
    modalPrimaryEl.textContent = options.primaryText || 'OK';
    modalDismissEl.textContent = options.dismissText || 'Cancel';

    if (options.secondaryText) {
      modalSecondaryEl.textContent = options.secondaryText;
      modalSecondaryEl.classList.remove('hidden');
    } else {
      modalSecondaryEl.classList.add('hidden');
    }

    modalEl.classList.remove('hidden');
    modalEl.setAttribute('aria-hidden', 'false');
    return new Promise(function(resolve) {
      modalResolver = resolve;
    });
  }

  modalPrimaryEl.addEventListener('click', function() {
    closeModal('primary');
  });
  modalSecondaryEl.addEventListener('click', function() {
    closeModal('secondary');
  });
  modalDismissEl.addEventListener('click', function() {
    closeModal('dismiss');
  });
  modalBackdropEl.addEventListener('click', function() {
    closeModal('dismiss');
  });
  document.addEventListener('keydown', function(event) {
    if (event.key === 'Escape') {
      closeModal('dismiss');
    }
  });

  async function postJson(url, payload) {
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    let data = {};
    try {
      data = await response.json();
    } catch (e) {
      data = { message: response.statusText || 'Request failed' };
    }
    if (!response.ok) {
      throw new Error(data.message || ('HTTP ' + response.status));
    }
    return data;
  }

  async function refreshFileList() {
    if (typeof window.reloadFileList === 'function') {
      try {
        await window.reloadFileList();
        return true;
      } catch (e) {
        return false;
      }
    }
    window.location.reload();
    return true;
  }

  async function resolveRenameTargetName(fileSize, suggestedName) {
    let candidate = String(suggestedName || '').trim();
    if (!candidate) {
      throw new Error('Server did not provide a rename suggestion.');
    }

    let attempts = 0;
    while (attempts < 2000) {
      const probe = await postJson('/.upload/check', {
        filename: candidate,
        file_size: fileSize,
        directory: currentDir,
      });
      if (!probe.exists && !probe.meta_exists) {
        return candidate;
      }
      if (!probe.rename_suggestion || probe.rename_suggestion === candidate) {
        throw new Error('Unable to generate an available renamed filename.');
      }
      candidate = String(probe.rename_suggestion);
      attempts += 1;
    }

    throw new Error('Too many rename attempts.');
  }

  async function uploadFile() {
    const file = fileInput.files && fileInput.files[0];
    if (!file) {
      setStatus('Please choose a file first.', true);
      setDetail('No file selected');
      setPickedFileLabel(null);
      return;
    }

    setWriteControlsDisabled(true);
    setStatus('Checking existing file...', false);
    setDetail('Target: ' + file.name + ' (' + formatBytes(file.size) + ')');
    setProgress(0, 100);

    try {
      const check = await postJson('/.upload/check', {
        filename: file.name,
        file_size: file.size,
        directory: currentDir,
      });

      let targetFilename = file.name;
      if (check.exists || check.meta_exists) {
        const action = await showChoiceModal({
          title: 'File Conflict',
          message:
            (check.message || 'A file with the same name already exists.') +
            '\\n\\nChoose Overwrite to replace it, or Rename to upload as a new filename.',
          primaryText: 'Rename',
          secondaryText: 'Overwrite',
          dismissText: 'Cancel'
        });
        if (action === 'primary') {
          targetFilename = await resolveRenameTargetName(file.size, check.rename_suggestion);
        } else if (action !== 'secondary') {
          setStatus('Upload canceled.', false);
          setDetail('Canceled by user');
          return;
        }
      }

      setDetail('Target: ' + targetFilename + ' (' + formatBytes(file.size) + ')');

      const init = await postJson('/.upload/init', {
        filename: targetFilename,
        file_size: file.size,
        directory: currentDir,
        mode: 'overwrite',
        chunk_size: chunkSize,
      });

      let bytesReceived = init.bytes_received || 0;
      setProgress(bytesReceived, file.size);

      const totalChunks = Math.ceil(file.size / chunkSize) || 1;
      for (let chunkIndex = 0; chunkIndex < totalChunks; chunkIndex += 1) {
        const start = chunkIndex * chunkSize;
        const end = Math.min(file.size, start + chunkSize);

        const blob = file.slice(start, end);
        const query = new URLSearchParams({
          filename: targetFilename,
          directory: currentDir,
          start: String(start),
          end: String(end),
          file_size: String(file.size),
        });

        setStatus('Uploading chunk ' + (chunkIndex + 1) + ' / ' + totalChunks + ' ...', false);
        const response = await fetch('/.upload/chunk?' + query.toString(), {
          method: 'POST',
          body: blob,
        });

        let chunkData = {};
        try {
          chunkData = await response.json();
        } catch (e) {
          chunkData = { message: response.statusText || 'Chunk upload failed' };
        }

        if (!response.ok) {
          throw new Error(chunkData.message || ('Chunk failed: HTTP ' + response.status));
        }

        bytesReceived = chunkData.bytes_received || bytesReceived;
        setProgress(bytesReceived, file.size);
      }

      setStatus('Upload complete. Refreshing file list...', false);
      setDetail('Saved as: ' + targetFilename);
      setProgress(file.size, file.size);
      const refreshed = await refreshFileList();
      if (!refreshed) {
        setStatus('Upload complete. Please refresh list manually.', false);
      } else {
        setStatus('Upload complete.', false);
      }
    } catch (err) {
      setStatus(err && err.message ? err.message : String(err), true);
      setDetail('Upload failed');
    } finally {
      setWriteControlsDisabled(false);
    }
  }

  async function deleteEntry(entryName) {
    const action = await showChoiceModal({
      title: 'Delete Entry',
      message:
        'You are about to delete: ' + entryName + '\\n\\n' +
        'Folders will be removed recursively and cannot be restored.',
      primaryText: 'Delete',
      dismissText: 'Cancel'
    });

    if (action !== 'primary') {
      return;
    }

    setWriteControlsDisabled(true);
    setStatus('Deleting ' + entryName + ' ...', false);
    setDetail('Delete in progress');

    try {
      const data = await postJson('/.upload/delete', {
        directory: currentDir,
        filename: entryName
      });
      const refreshed = await refreshFileList();
      if (!refreshed) {
        setStatus((data.message || ('Deleted ' + entryName)) + ' (list refresh failed)', false);
      } else {
        setStatus(data.message || ('Deleted ' + entryName), false);
      }
      setDetail('Delete completed');
    } catch (err) {
      setStatus(err && err.message ? err.message : String(err), true);
      setDetail('Delete failed');
    } finally {
      setWriteControlsDisabled(false);
    }
  }

  pickFileBtn.addEventListener('click', function() {
    fileInput.click();
  });

  fileInput.addEventListener('change', function() {
    const file = fileInput.files && fileInput.files[0];
    setPickedFileLabel(file || null);
    if (file) {
      setDetail('Selected: ' + formatBytes(file.size));
      setStatus('Ready to upload.', false);
    }
  });

  uploadBtn.addEventListener('click', function() {
    uploadFile();
  });

  document.addEventListener('click', function(event) {
    const target = event.target;
    if (!target) {
      return;
    }
    const button = target.closest ? target.closest('.delete-btn') : null;
    if (!button) {
      return;
    }
    const entryName = button.getAttribute('data-delete-name');
    if (!entryName) {
      return;
    }
    deleteEntry(entryName);
  });
})();
</script>
"""
    return template.replace("__CURRENT_DIR__", json.dumps(request_path)).replace(
        "__CHUNK_SIZE__", str(chunk_size)
    )
