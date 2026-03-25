// Recall Space — Client v0.3
// ============================================================================

const API = '';
let memories = [];
let collections = [];
let stats = {};
let activeFilter = 'all';
let activeCollection = null;
let currentQuery = '';
let searchTimeout = null;

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', () => {
    loadMemories();
    loadStats();
    loadCollections();
    loadDashboard();
    checkBrainStatus();
    setupEventListeners();
    setupVoiceRecorder();
    setInterval(checkBrainStatus, 30000);
    setInterval(loadStats, 15000);
    setInterval(() => {
        if (stats.pending_jobs > 0) { loadMemories(); loadStats(); loadDashboard(); }
    }, 5000);
});

// ---------------------------------------------------------------------------
// API helper
// ---------------------------------------------------------------------------
async function api(path, opts = {}) {
    const key = localStorage.getItem('recall_api_key');
    if (key) opts.headers = { ...opts.headers, 'X-API-Key': key };
    return fetch(`${API}${path}`, opts);
}

// ---------------------------------------------------------------------------
// Brain status
// ---------------------------------------------------------------------------
async function checkBrainStatus() {
    const el = document.getElementById('brain-status');
    if (!el) return;
    el.classList.add('checking');
    try {
        const res = await api('/api/worker-status');
        const data = await res.json();
        el.classList.remove('checking');
        if (data.online) {
            el.classList.add('online');
            el.title = `AI worker online — ${data.ollama_model || 'connected'}`;
        } else {
            el.classList.remove('online');
            el.title = `AI worker offline — ${data.reason || 'unreachable'}`;
        }
    } catch {
        el.classList.remove('checking', 'online');
        el.title = 'AI worker offline';
    }
}

// ---------------------------------------------------------------------------
// Data fetching
// ---------------------------------------------------------------------------
async function loadMemories() {
    const params = new URLSearchParams();
    params.set('limit', '100');
    if (currentQuery) params.set('q', currentQuery);
    if (activeFilter !== 'all') params.set('type', activeFilter);
    if (activeCollection) params.set('collection_id', activeCollection);

    try {
        const res = await api(`/api/memories?${params}`);
        const data = await res.json();
        memories = data.memories || [];
        renderTimeline();
        renderSearchCount(data.total);
    } catch (err) {
        console.error('Failed to load:', err);
    }
}

async function loadStats() {
    try {
        const res = await api('/api/stats');
        stats = await res.json();
        renderStats();
    } catch {}
}

async function loadCollections() {
    try {
        const res = await api('/api/collections');
        const data = await res.json();
        collections = data.collections || [];
        renderCollections();
        renderCollectionSelect();
    } catch {}
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------
function renderStats() {
    const el = document.getElementById('stats');
    if (!el) return;
    const brain = document.getElementById('brain-status');
    const brainHtml = brain ? brain.outerHTML : '';
    const rem = stats.upcoming_reminders
        ? `<span><span class="num">${stats.upcoming_reminders}</span> reminders</span>` : '';
    el.innerHTML = `
        <span><span class="num">${stats.total_memories || 0}</span> memories</span>
        <span><span class="num">${stats.pending_jobs || 0}</span> pending</span>
        ${rem}
        <span class="brain-status" id="brain-status" title="checking...">🧠</span>
    `;
    checkBrainStatus();
}

function renderSearchCount(total) {
    const el = document.getElementById('search-count');
    if (!el) return;
    const shell = document.querySelector('.shell');
    if (currentQuery) {
        el.textContent = `${memories.length} result${memories.length !== 1 ? 's' : ''} for "${currentQuery}"`;
        shell?.classList.add('search-active');
    } else {
        el.textContent = '';
        shell?.classList.remove('search-active');
    }
}

function renderTimeline() {
    const container = document.getElementById('timeline');
    if (!container) return;

    if (memories.length === 0) {
        const msg = currentQuery ? `no results for "${esc(currentQuery)}"` : 'no memories yet. capture something.';
        container.innerHTML = `<div class="empty-state"><div class="icon">◇</div><p>${msg}</p></div>`;
        return;
    }

    const groups = {};
    for (const m of memories) {
        const date = m.created_at?.split('T')[0] || 'unknown';
        if (!groups[date]) groups[date] = [];
        groups[date].push(m);
    }

    let html = '';
    for (const [date, items] of Object.entries(groups)) {
        html += `<div class="day-group"><div class="day-label">${formatDateLabel(date)}</div>`;
        for (const m of items) html += renderMemoryCard(m);
        html += '</div>';
    }
    container.innerHTML = html;
}

function renderMemoryCard(m) {
    const time = m.created_at
        ? new Date(m.created_at + 'Z').toLocaleTimeString('da-DK', { hour: '2-digit', minute: '2-digit' })
        : '';
    let title = m.title || m.ai_summary?.substring(0, 60) || m.user_note?.substring(0, 60)
        || m.url || m.original_filename || 'untitled';

    let thumbHtml = `<span class="memory-type" data-type="${m.type}">${m.type}</span>`;
    if (m.type === 'screenshot' && m.file_path) {
        thumbHtml = `<img class="memory-thumb" src="/uploads/${m.file_path}" alt="" loading="lazy">`;
    }

    // Tags
    let tagsHtml = '';
    if (m.ai_tags) {
        try {
            const tags = JSON.parse(m.ai_tags);
            if (tags.length) {
                tagsHtml = '<div class="tags">'
                    + tags.slice(0, 4).map(t => `<span class="tag-inline">${esc(t)}</span>`).join('')
                    + '</div>';
            }
        } catch {}
    }

    // Collection badge
    let collBadge = '';
    if (m.collection_id) {
        const coll = collections.find(c => c.id === m.collection_id);
        if (coll) collBadge = `<span class="collection-badge">${esc(coll.name)}</span>`;
    }

    // Highlight search terms in title and summary
    const displayTitle = currentQuery ? highlightText(title, currentQuery) : esc(title);
    const displaySummary = m.ai_summary
        ? (currentQuery ? highlightText(m.ai_summary, currentQuery) : esc(m.ai_summary))
        : '';

    return `
    <a class="memory-card" href="/memory/${m.id}">
        ${thumbHtml}
        <div class="memory-body">
            <h3>${displayTitle} ${collBadge}</h3>
            ${m.user_note ? `<div class="note">${esc(m.user_note)}</div>` : ''}
            ${displaySummary ? `<div class="summary">${displaySummary}</div>` : ''}
            ${tagsHtml}
        </div>
        <div class="memory-meta">
            <span class="time">${time}</span>
            <span class="memory-status" data-status="${m.processing_status}" title="${m.processing_status}"></span>
        </div>
    </a>`;
}

function highlightText(text, query) {
    const escaped = esc(text);
    if (!query) return escaped;
    const words = query.split(/\s+/).filter(w => w.length > 1);
    let result = escaped;
    for (const word of words) {
        const re = new RegExp(`(${word.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi');
        result = result.replace(re, '<span class="highlight">$1</span>');
    }
    return result;
}

// ---------------------------------------------------------------------------
// Collections
// ---------------------------------------------------------------------------
function renderCollections() {
    const el = document.getElementById('collections-list');
    if (!el) return;

    let html = `<button class="collection-chip ${!activeCollection ? 'active' : ''}"
        onclick="filterCollection(null)">all</button>`;

    for (const c of collections) {
        const isActive = activeCollection === c.id;
        html += `<button class="collection-chip ${isActive ? 'active' : ''}"
            onclick="filterCollection(${c.id})">
            <span class="dot" style="background:${esc(c.color)}"></span>
            ${esc(c.name)}
            <span class="count">${c.memory_count || 0}</span>
            <span class="remove" onclick="event.stopPropagation();deleteCollection(${c.id})">×</span>
        </button>`;
    }
    el.innerHTML = html;
}

function renderCollectionSelect() {
    const sel = document.getElementById('capture-collection');
    if (!sel) return;
    sel.innerHTML = '<option value="">none</option>'
        + collections.map(c => `<option value="${c.id}">${esc(c.name)}</option>`).join('');
}

function filterCollection(id) {
    activeCollection = id;
    renderCollections();
    loadMemories();
}

async function addCollection() {
    const input = document.getElementById('new-collection-name');
    const name = input?.value.trim();
    if (!name) return;
    try {
        await api('/api/collections', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name }),
        });
        input.value = '';
        loadCollections();
    } catch {}
}

async function deleteCollection(id) {
    if (!confirm('Delete this collection? Memories will be kept.')) return;
    try {
        await api(`/api/collections/${id}`, { method: 'DELETE' });
        if (activeCollection === id) activeCollection = null;
        loadCollections();
        loadMemories();
    } catch {}
}

// ---------------------------------------------------------------------------
// Dashboard
// ---------------------------------------------------------------------------
let dashboardData = {};

async function loadDashboard() {
    try {
        const res = await api('/api/dashboard');
        dashboardData = await res.json();
        renderDashboard();
    } catch {}
}

function renderDashboard() {
    const d = dashboardData;
    const totalItems = (d.reminders?.length || 0) + (d.actions?.length || 0);
    const countEl = document.getElementById('dashboard-count');
    if (countEl) countEl.textContent = totalItems > 0 ? totalItems : '';

    // Reminders
    const remEl = document.getElementById('dash-reminders');
    if (remEl) {
        if (d.reminders?.length) {
            remEl.innerHTML = `<div class="dash-label">upcoming reminders</div>`
                + d.reminders.map(r => {
                    const time = r.remind_at ? formatReminderTime(r.remind_at) : '';
                    return `<div class="dash-item">
                        <a href="/memory/${r.memory_id}">${esc(r.title)}</a>
                        <span class="dash-time">${time}</span>
                    </div>`;
                }).join('');
        } else {
            remEl.innerHTML = '';
        }
    }

    // Actions
    const actEl = document.getElementById('dash-actions');
    if (actEl) {
        if (d.actions?.length) {
            actEl.innerHTML = `<div class="dash-label">action items</div>`
                + d.actions.map(a => `<div class="dash-item ${a.done ? 'done' : ''}">
                    <input type="checkbox" class="action-check" ${a.done ? 'checked' : ''}
                        onchange="toggleAction(${a.id}, this.checked)">
                    <a href="/memory/${a.memory_id}">${esc(a.text)}</a>
                    ${a.memory_title ? `<span class="dash-source">${esc(a.memory_title)}</span>` : ''}
                </div>`).join('');
        } else {
            actEl.innerHTML = '';
        }
    }

    // Processing
    const procEl = document.getElementById('dash-processing');
    if (procEl) {
        if (d.processing_count > 0) {
            procEl.innerHTML = `<div class="dash-processing">
                <span class="spin"></span>
                ${d.processing_count} memor${d.processing_count === 1 ? 'y' : 'ies'} processing...
            </div>`;
        } else {
            procEl.innerHTML = '';
        }
    }

    // Auto-open dashboard if there are items
    if (totalItems > 0 || d.processing_count > 0) {
        document.getElementById('dashboard')?.classList.add('open');
    }
}

function formatReminderTime(isoStr) {
    try {
        const d = new Date(isoStr + (isoStr.includes('Z') ? '' : 'Z'));
        const now = new Date();
        const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
        const tomorrow = new Date(today); tomorrow.setDate(tomorrow.getDate() + 1);
        const reminderDate = new Date(d.getFullYear(), d.getMonth(), d.getDate());

        const time = d.toLocaleTimeString('da-DK', { hour: '2-digit', minute: '2-digit' });
        if (reminderDate.getTime() === today.getTime()) return `today ${time}`;
        if (reminderDate.getTime() === tomorrow.getTime()) return `tomorrow ${time}`;
        return d.toLocaleDateString('da-DK', { day: 'numeric', month: 'short' }) + ' ' + time;
    } catch { return isoStr; }
}

async function toggleAction(id, done) {
    try {
        await api(`/api/actions/${id}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ done }),
        });
        loadDashboard();
    } catch {}
}

// ---------------------------------------------------------------------------
// Quick capture
// ---------------------------------------------------------------------------
async function quickTextCapture(text) {
    if (!text.trim()) return;
    const fd = new FormData();
    fd.append('raw_text', text);
    try {
        const res = await api('/api/memories', { method: 'POST', body: fd });
        if (res.ok) {
            document.getElementById('quick-input').value = '';
            loadMemories();
            loadStats();
            loadDashboard();
            // Brief flash feedback
            const input = document.getElementById('quick-input');
            input.style.borderColor = 'var(--accent)';
            setTimeout(() => input.style.borderColor = '', 500);
        }
    } catch (err) { console.error('Quick capture error:', err); }
}

// ---------------------------------------------------------------------------
// Event listeners
// ---------------------------------------------------------------------------
function setupEventListeners() {
    document.getElementById('btn-screenshot')?.addEventListener('click', () => openModal('screenshot'));
    document.getElementById('btn-url')?.addEventListener('click', () => openModal('url'));
    document.getElementById('btn-voice')?.addEventListener('click', () => openModal('voice'));

    document.getElementById('modal-overlay')?.addEventListener('click', (e) => {
        if (e.target.id === 'modal-overlay') closeModal();
    });
    document.getElementById('btn-cancel')?.addEventListener('click', closeModal);
    document.getElementById('btn-save')?.addEventListener('click', submitCapture);

    // Quick capture — Enter to submit
    document.getElementById('quick-input')?.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            quickTextCapture(e.target.value);
        }
    });

    // Dashboard toggle
    document.getElementById('dashboard-toggle')?.addEventListener('click', () => {
        document.getElementById('dashboard')?.classList.toggle('open');
    });

    // Search
    document.getElementById('search-input')?.addEventListener('input', (e) => {
        clearTimeout(searchTimeout);
        searchTimeout = setTimeout(() => {
            currentQuery = e.target.value.trim();
            loadMemories();
        }, 300);
    });

    // Filters
    document.querySelectorAll('.filter-tab').forEach(tab => {
        tab.addEventListener('click', () => {
            document.querySelectorAll('.filter-tab').forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            activeFilter = tab.dataset.type;
            loadMemories();
        });
    });

    // Collections toggle
    document.getElementById('collections-toggle')?.addEventListener('click', () => {
        const panel = document.getElementById('collections-panel');
        const toggle = document.getElementById('collections-toggle');
        panel?.classList.toggle('open');
        toggle?.classList.toggle('active');
    });

    // Add collection
    document.getElementById('btn-add-collection')?.addEventListener('click', addCollection);
    document.getElementById('new-collection-name')?.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') addCollection();
    });

    // Drop zone
    const dropZone = document.getElementById('drop-zone');
    const fileInput = document.getElementById('file-input');
    dropZone?.addEventListener('click', () => fileInput?.click());
    dropZone?.addEventListener('dragover', (e) => { e.preventDefault(); dropZone.classList.add('dragover'); });
    dropZone?.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
    dropZone?.addEventListener('drop', (e) => {
        e.preventDefault(); dropZone.classList.remove('dragover');
        if (e.dataTransfer.files.length) {
            fileInput.files = e.dataTransfer.files;
            dropZone.textContent = e.dataTransfer.files[0].name;
            dropZone.classList.add('has-file');
        }
    });
    fileInput?.addEventListener('change', () => {
        if (fileInput.files.length) {
            dropZone.textContent = fileInput.files[0].name;
            dropZone.classList.add('has-file');
        }
    });

    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });

    // Paste capture
    document.addEventListener('paste', async (e) => {
        const items = e.clipboardData?.items;
        if (!items) return;
        for (const item of items) {
            if (item.type.startsWith('image/')) {
                e.preventDefault();
                await quickCapture(item.getAsFile());
                return;
            }
        }
    });
}

// ---------------------------------------------------------------------------
// Modal
// ---------------------------------------------------------------------------
let currentModalType = 'screenshot';

function openModal(type) {
    currentModalType = type;
    const overlay = document.getElementById('modal-overlay');
    const groups = { file: 'file-group', url: 'url-group', text: 'text-group', voice: 'voice-group' };
    const titles = { screenshot: '// capture file', text: '// capture text', url: '// capture url', voice: '// capture voice' };

    Object.values(groups).forEach(id => document.getElementById(id).style.display = 'none');
    document.getElementById('modal-title').textContent = titles[type] || '// capture';

    const target = type === 'screenshot' ? 'file' : type;
    if (groups[target]) document.getElementById(groups[target]).style.display = 'block';

    // Refresh collection dropdown
    renderCollectionSelect();

    overlay.classList.add('active');
    setTimeout(() => {
        overlay.querySelector('.form-group:not([style*="display: none"]) input, .form-group:not([style*="display: none"]) textarea')?.focus();
    }, 100);
}

function closeModal() {
    document.getElementById('modal-overlay')?.classList.remove('active');
    document.getElementById('capture-note').value = '';
    document.getElementById('capture-title').value = '';
    document.getElementById('capture-url').value = '';
    document.getElementById('capture-text').value = '';
    document.getElementById('capture-collection').value = '';
    const dz = document.getElementById('drop-zone');
    if (dz) { dz.textContent = 'drop file here or click to browse'; dz.classList.remove('has-file'); }
    document.getElementById('file-input').value = '';
    stopRecording();
}

// ---------------------------------------------------------------------------
// Submit
// ---------------------------------------------------------------------------
async function submitCapture() {
    const fd = new FormData();
    const note = document.getElementById('capture-note')?.value;
    const title = document.getElementById('capture-title')?.value;
    const collId = document.getElementById('capture-collection')?.value;
    if (note) fd.append('user_note', note);
    if (title) fd.append('title', title);
    if (collId) fd.append('collection_id', collId);

    switch (currentModalType) {
        case 'screenshot': {
            const fi = document.getElementById('file-input');
            if (!fi?.files.length) return;
            fd.append('file', fi.files[0]);
            break;
        }
        case 'text': {
            const t = document.getElementById('capture-text')?.value;
            if (!t?.trim()) return;
            fd.append('raw_text', t);
            break;
        }
        case 'url': {
            const u = document.getElementById('capture-url')?.value;
            if (!u?.trim()) return;
            fd.append('url', u);
            break;
        }
        case 'voice': {
            if (!recordedBlob) return;
            fd.append('file', recordedBlob, 'voice-note.webm');
            break;
        }
    }

    try {
        const res = await api('/api/memories', { method: 'POST', body: fd });
        if (res.ok) { closeModal(); loadMemories(); loadStats(); loadCollections(); }
    } catch (err) { console.error('Capture error:', err); }
}

async function quickCapture(blob) {
    const fd = new FormData();
    fd.append('file', blob, `paste-${Date.now()}.${blob.type.split('/')[1] || 'png'}`);
    try {
        const res = await api('/api/memories', { method: 'POST', body: fd });
        if (res.ok) {
            loadMemories(); loadStats();
            document.body.style.outline = '2px solid var(--accent)';
            setTimeout(() => document.body.style.outline = '', 300);
        }
    } catch {}
}

// ---------------------------------------------------------------------------
// Voice recorder
// ---------------------------------------------------------------------------
let mediaRecorder = null, recordedChunks = [], recordedBlob = null, recordingStart = null, recordingTimer = null;

function setupVoiceRecorder() {
    document.getElementById('record-btn')?.addEventListener('click', async () => {
        if (mediaRecorder?.state === 'recording') stopRecording();
        else await startRecording();
    });
}

async function startRecording() {
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        recordedChunks = []; recordedBlob = null;
        mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm;codecs=opus' });
        mediaRecorder.ondataavailable = (e) => { if (e.data.size > 0) recordedChunks.push(e.data); };
        mediaRecorder.onstop = () => {
            recordedBlob = new Blob(recordedChunks, { type: 'audio/webm' });
            stream.getTracks().forEach(t => t.stop());
        };
        mediaRecorder.start(100);
        recordingStart = Date.now();
        document.getElementById('record-btn')?.classList.add('recording');
        recordingTimer = setInterval(() => {
            const el = document.getElementById('record-time');
            if (!el || !recordingStart) return;
            const s = Math.floor((Date.now() - recordingStart) / 1000);
            el.textContent = `${String(Math.floor(s/60)).padStart(2,'0')}:${String(s%60).padStart(2,'0')}`;
        }, 250);
    } catch (err) { console.error('Mic denied:', err); }
}

function stopRecording() {
    if (mediaRecorder?.state === 'recording') mediaRecorder.stop();
    document.getElementById('record-btn')?.classList.remove('recording');
    clearInterval(recordingTimer);
}

// ---------------------------------------------------------------------------
// Utils
// ---------------------------------------------------------------------------
function formatDateLabel(d) {
    const date = new Date(d + 'T00:00:00');
    const now = new Date();
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const yesterday = new Date(today); yesterday.setDate(yesterday.getDate() - 1);
    if (date >= today) return 'today';
    if (date >= yesterday) return 'yesterday';
    return date.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' });
}

function esc(s) {
    if (!s) return '';
    return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
