// Recall Space — Client
// ============================================================================

const API = '';
let memories = [];
let stats = {};
let activeFilter = 'all';
let searchTimeout = null;
let refreshInterval = null;

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', () => {
    loadMemories();
    loadStats();
    checkBrainStatus();
    setupEventListeners();
    setupVoiceRecorder();
    // Poll brain status every 30s, stats every 15s
    setInterval(checkBrainStatus, 30000);
    setInterval(loadStats, 15000);
    // Auto-refresh timeline if there are pending jobs
    refreshInterval = setInterval(() => {
        if (stats.pending_jobs > 0) loadMemories();
    }, 5000);
});

// ---------------------------------------------------------------------------
// API helper (adds auth header if configured)
// ---------------------------------------------------------------------------
async function api(path, opts = {}) {
    // API key can be stored in localStorage for convenience
    const key = localStorage.getItem('recall_api_key');
    if (key) {
        opts.headers = { ...opts.headers, 'X-API-Key': key };
    }
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
async function loadMemories(query = null, type = null) {
    const params = new URLSearchParams();
    params.set('limit', '100');
    if (query) params.set('q', query);
    if (type && type !== 'all') params.set('type', type);

    try {
        const res = await api(`/api/memories?${params}`);
        const data = await res.json();
        memories = data.memories || [];
        renderTimeline();
    } catch (err) {
        console.error('Failed to load memories:', err);
    }
}

async function loadStats() {
    try {
        const res = await api('/api/stats');
        stats = await res.json();
        renderStats();
    } catch (err) {
        console.error('Failed to load stats:', err);
    }
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------
function renderStats() {
    const el = document.getElementById('stats');
    if (!el) return;
    const brain = el.querySelector('.brain-status')?.outerHTML || '';
    const reminders = stats.upcoming_reminders
        ? `<span><span class="num">${stats.upcoming_reminders}</span> reminders</span>`
        : '';
    el.innerHTML = `
        <span><span class="num">${stats.total_memories || 0}</span> memories</span>
        <span><span class="num">${stats.pending_jobs || 0}</span> pending</span>
        ${reminders}
        ${brain}
    `;
    checkBrainStatus();
}

function renderTimeline() {
    const container = document.getElementById('timeline');
    if (!container) return;

    if (memories.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <div class="icon">◇</div>
                <p>no memories yet. capture something.</p>
            </div>`;
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
        html += `<div class="day-group">
            <div class="day-label">${formatDateLabel(date)}</div>`;
        for (const m of items) html += renderMemoryCard(m);
        html += '</div>';
    }
    container.innerHTML = html;
}

function renderMemoryCard(m) {
    const time = m.created_at
        ? new Date(m.created_at + 'Z').toLocaleTimeString('da-DK', { hour: '2-digit', minute: '2-digit' })
        : '';
    const title = m.title || m.ai_summary?.substring(0, 60) || m.user_note?.substring(0, 60)
        || m.url || m.original_filename || 'untitled';

    let thumbHtml = `<span class="memory-type" data-type="${m.type}">${m.type}</span>`;
    if (m.type === 'screenshot' && m.file_path) {
        thumbHtml = `<img class="memory-thumb" src="/uploads/${m.file_path}" alt="" loading="lazy">`;
    }

    return `
    <a class="memory-card" href="/memory/${m.id}">
        ${thumbHtml}
        <div class="memory-body">
            <h3>${esc(title)}</h3>
            ${m.user_note ? `<div class="note">${esc(m.user_note)}</div>` : ''}
            ${m.ai_summary ? `<div class="summary">${esc(m.ai_summary)}</div>` : ''}
        </div>
        <div class="memory-meta">
            <span class="time">${time}</span>
            <span class="memory-status" data-status="${m.processing_status}" title="${m.processing_status}"></span>
        </div>
    </a>`;
}

function formatDateLabel(dateStr) {
    const d = new Date(dateStr + 'T00:00:00');
    const now = new Date();
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const yesterday = new Date(today);
    yesterday.setDate(yesterday.getDate() - 1);
    if (d >= today) return 'today';
    if (d >= yesterday) return 'yesterday';
    return d.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' });
}

// ---------------------------------------------------------------------------
// Event listeners
// ---------------------------------------------------------------------------
function setupEventListeners() {
    document.getElementById('btn-screenshot')?.addEventListener('click', () => openModal('screenshot'));
    document.getElementById('btn-text')?.addEventListener('click', () => openModal('text'));
    document.getElementById('btn-url')?.addEventListener('click', () => openModal('url'));
    document.getElementById('btn-voice')?.addEventListener('click', () => openModal('voice'));

    document.getElementById('modal-overlay')?.addEventListener('click', (e) => {
        if (e.target.id === 'modal-overlay') closeModal();
    });
    document.getElementById('btn-cancel')?.addEventListener('click', closeModal);
    document.getElementById('btn-save')?.addEventListener('click', submitCapture);

    const searchInput = document.getElementById('search-input');
    searchInput?.addEventListener('input', (e) => {
        clearTimeout(searchTimeout);
        searchTimeout = setTimeout(() => {
            loadMemories(e.target.value || null, activeFilter);
        }, 300);
    });

    document.querySelectorAll('.filter-tab').forEach(tab => {
        tab.addEventListener('click', () => {
            document.querySelectorAll('.filter-tab').forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            activeFilter = tab.dataset.type;
            loadMemories(document.getElementById('search-input')?.value || null, activeFilter);
        });
    });

    // Drop zone
    const dropZone = document.getElementById('drop-zone');
    const fileInput = document.getElementById('file-input');
    dropZone?.addEventListener('click', () => fileInput?.click());
    dropZone?.addEventListener('dragover', (e) => { e.preventDefault(); dropZone.classList.add('dragover'); });
    dropZone?.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
    dropZone?.addEventListener('drop', (e) => {
        e.preventDefault();
        dropZone.classList.remove('dragover');
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
    if (note) fd.append('user_note', note);
    if (title) fd.append('title', title);

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
        if (res.ok) {
            closeModal();
            loadMemories();
            loadStats();
        }
    } catch (err) {
        console.error('Capture error:', err);
    }
}

async function quickCapture(blob) {
    const fd = new FormData();
    fd.append('file', blob, `paste-${Date.now()}.${blob.type.split('/')[1] || 'png'}`);
    try {
        const res = await api('/api/memories', { method: 'POST', body: fd });
        if (res.ok) {
            loadMemories();
            loadStats();
            document.body.style.outline = '2px solid var(--accent)';
            setTimeout(() => document.body.style.outline = '', 300);
        }
    } catch (err) {
        console.error('Quick capture error:', err);
    }
}

// ---------------------------------------------------------------------------
// Voice recorder
// ---------------------------------------------------------------------------
let mediaRecorder = null;
let recordedChunks = [];
let recordedBlob = null;
let recordingStart = null;
let recordingTimer = null;

function setupVoiceRecorder() {
    document.getElementById('record-btn')?.addEventListener('click', async () => {
        if (mediaRecorder?.state === 'recording') stopRecording();
        else await startRecording();
    });
}

async function startRecording() {
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        recordedChunks = [];
        recordedBlob = null;
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
    } catch (err) {
        console.error('Microphone access denied:', err);
    }
}

function stopRecording() {
    if (mediaRecorder?.state === 'recording') mediaRecorder.stop();
    document.getElementById('record-btn')?.classList.remove('recording');
    clearInterval(recordingTimer);
}

// ---------------------------------------------------------------------------
// Utils
// ---------------------------------------------------------------------------
function esc(s) {
    if (!s) return '';
    return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
