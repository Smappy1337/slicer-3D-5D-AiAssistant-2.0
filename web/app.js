/* ============================================================
   AI Assistant — frontend (стриминг + ChatGPT-UI)
   ============================================================ */

const messagesContainer = document.getElementById('messages');
const welcomeEl         = document.getElementById('welcome');
const userInput         = document.getElementById('userInput');
const sendBtn           = document.getElementById('sendBtn');
const fileInput         = document.getElementById('fileInput');
const attachBtn         = document.getElementById('attachBtn');
const chatContainer     = document.getElementById('chat-container');
const clearChatBtn      = document.getElementById('clearChatBtn');
const resetContextBtn   = document.getElementById('resetContextBtn');
const statusEl          = document.getElementById('status');

const FETCH_TIMEOUT_MS  = 180000;
const UPLOAD_TIMEOUT_MS = 180000;

let busy = false;

/* ============================================================
   Утилиты
   ============================================================ */
function setStatus(color) {
    if (statusEl) statusEl.style.color = color;
}

function setBusy(state) {
    busy = state;
    sendBtn.disabled = state;
    attachBtn.disabled = state;
    userInput.disabled = state;
}

function nowTime() {
    return new Date().toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
}

async function readError(response) {
    let msg = `HTTP ${response.status}`;
    try {
        const err = await response.json();
        msg = err.error || msg;
    } catch (_) {}
    return msg;
}

function autoGrow() {
    userInput.style.height = 'auto';
    userInput.style.height = Math.min(userInput.scrollHeight, 180) + 'px';
}
userInput.addEventListener('input', autoGrow);

/* ============================================================
   Markdown с санитизацией (безопасный рендер)
   ============================================================ */
function parseMarkdownSafe(text) {
    // 1) marked + DOMPurify
    if (typeof marked !== 'undefined' && typeof DOMPurify !== 'undefined') {
        try {
            let html;
            if (typeof marked.parse === 'function') html = marked.parse(text);
            else if (typeof marked === 'function') html = marked(text);
            else return null;
            return DOMPurify.sanitize(html, {
                USE_PROFILES: { html: true },
                FORBID_TAGS: ['style', 'iframe', 'form', 'input', 'object', 'embed'],
                FORBID_ATTR: ['onerror', 'onload', 'onclick', 'onmouseover'],
            });
        } catch (_) {}
    }
    // 2) Минимальный fallback (офлайн-режим)
    try {
        let esc = text
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;');
        esc = esc
            .replace(/```([\s\S]*?)```/g, (_, code) =>
                `<pre><code>${code.replace(/&/g,'&amp;').replace(/</g,'&lt;')}</code></pre>`)
            .replace(/`([^`\n]+)`/g, '<code>$1</code>')
            .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
            .replace(/\*([^*\n]+)\*/g, '<em>$1</em>')
            .replace(/\n/g, '<br>');
        return esc;
    } catch (_) {
        return null;
    }
}

/* ============================================================
   Приветствие
   ============================================================ */
function hideWelcome() {
    if (welcomeEl) welcomeEl.classList.add('hidden');
}
function showWelcomeIfEmpty() {
    if (!welcomeEl) return;
    const hasMsgs = messagesContainer.querySelector('.msg');
    welcomeEl.classList.toggle('hidden', !!hasMsgs);
}

document.querySelectorAll('.suggestion').forEach(btn => {
    btn.addEventListener('click', () => {
        const prompt = btn.getAttribute('data-prompt') || btn.textContent.trim();
        userInput.value = prompt;
        autoGrow();
        userInput.focus();
    });
});

/* ============================================================
   Проверка зависимостей
   ============================================================ */
async function checkDependencies() {
    try {
        const resp = await fetch('/api/deps');
        if (!resp.ok) return;
        const data = await resp.json();

        const failed = [];
        for (const name of ['pypdf', 'python-docx', 'python-pptx']) {
            const item = data[name];
            if (item && !item.ok) {
                failed.push(`• ${name}${item.error ? ' — ' + item.error : ''}`);
            }
        }
        if (data.installing) {
            appendSystem('Устанавливаются библиотеки для документов. Это займёт 1–2 минуты.');
        } else if (failed.length > 0) {
            appendSystem(
                'Не удалось установить:\n' + failed.join('\n') +
                `\n\nУстановите вручную:\n  ${data.manual_command || 'pip install pypdf python-docx python-pptx'}`);
        }
    } catch (_) {}
}

/* ============================================================
   Отрисовка сообщений
   ============================================================ */
function createMessageEl(role, initialText = '') {
    hideWelcome();

    const wrap = document.createElement('div');
    wrap.className = `msg ${role}`;

    const avatar = document.createElement('div');
    avatar.className = 'msg-avatar';
    avatar.textContent = role === 'user' ? 'В' : role === 'system' ? '!' : '✦';

    const body = document.createElement('div');
    body.className = 'msg-body';

    const author = document.createElement('div');
    author.className = 'msg-author';
    author.innerHTML =
        `<span>${role === 'user' ? 'Вы' : role === 'system' ? 'Система' : 'AI Assistant'}</span>` +
        `<span class="msg-time">${nowTime()}</span>`;

    const content = document.createElement('div');
    content.className = 'msg-content';

    body.appendChild(author);
    body.appendChild(content);

    if (role === 'ai') {
        const actions = document.createElement('div');
        actions.className = 'msg-actions';
        const copyBtn = document.createElement('button');
        copyBtn.className = 'msg-action';
        copyBtn.textContent = '📋 Копировать';
        copyBtn.addEventListener('click', () => {
            const txt = content.innerText;
            navigator.clipboard.writeText(txt).then(() => {
                copyBtn.textContent = '✓ Скопировано';
                setTimeout(() => copyBtn.textContent = '📋 Копировать', 1500);
            }).catch(() => {});
        });
        actions.appendChild(copyBtn);
        body.appendChild(actions);
    }

    wrap.appendChild(avatar);
    wrap.appendChild(body);
    messagesContainer.appendChild(wrap);

    if (initialText) updateMessageContent(content, initialText);
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
    return { wrap, content };
}

function updateMessageContent(contentEl, text) {
    const safe = parseMarkdownSafe(text);
    if (safe !== null) {
        contentEl.innerHTML = safe;
    } else {
        contentEl.textContent = text;
    }
}

function appendUser(text) {
    const { content } = createMessageEl('user');
    content.textContent = text;
}

function appendSystem(text) {
    const { content } = createMessageEl('system');
    content.textContent = text;
}

function appendAIMessage(text) {
    const { content } = createMessageEl('ai');
    updateMessageContent(content, text);
}

/* ============================================================
   Отправка сообщения (стриминг)
   ============================================================ */
async function sendMessage() {
    if (busy) return;
    const text = userInput.value.trim();
    if (!text) return;

    appendUser(text);
    userInput.value = '';
    autoGrow();
    setBusy(true);
    setStatus('#f59e0b');

    const { content } = createMessageEl('ai');
    const thinking = document.createElement('div');
    thinking.className = 'thinking';
    thinking.innerHTML = '<span></span><span></span><span></span>';
    content.appendChild(thinking);

    let fullText = '';
    let firstChunk = true;
    let errored = false;

    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), FETCH_TIMEOUT_MS);

    try {
        const resp = await fetch('/api/message/stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text }),
            signal: ctrl.signal,
        });

        if (!resp.ok || !resp.body) {
            throw new Error(await readError(resp));
        }

        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });

            let nl;
            while ((nl = buffer.indexOf('\n')) >= 0) {
                const line = buffer.slice(0, nl).trim();
                buffer = buffer.slice(nl + 1);
                if (!line) continue;

                let evt;
                try { evt = JSON.parse(line); } catch (_) { continue; }

                if (evt.type === 'chunk') {
                    if (firstChunk) {
                        content.innerHTML = '';
                        firstChunk = false;
                    }
                    fullText += evt.text;
                    updateMessageContent(content, fullText);
                    messagesContainer.scrollTop = messagesContainer.scrollHeight;
                } else if (evt.type === 'tool') {
                    if (firstChunk) {
                        content.innerHTML = '';
                        firstChunk = false;
                    }
                    fullText += (fullText ? '\n\n' : '') + `⚙️ **${evt.name}** — ${evt.result}`;
                    updateMessageContent(content, fullText);
                    messagesContainer.scrollTop = messagesContainer.scrollHeight;
                } else if (evt.type === 'newline') {
                    fullText += '\n\n';
                    updateMessageContent(content, fullText);
                } else if (evt.type === 'error') {
                    errored = true;
                    appendSystem(evt.text);
                } else if (evt.type === 'done') {
                    /* финализация */
                }
            }
        }

        if (!fullText && !errored) {
            updateMessageContent(content, 'Готово.');
        }
        if (!fullText && errored) {
            content.closest('.msg')?.remove();
        }

        setStatus(errored ? '#ef4444' : '#10b981');
    } catch (error) {
        if (error.name === 'AbortError') {
            appendSystem(`Таймаут ответа (${FETCH_TIMEOUT_MS / 1000} с). Попробуйте ещё раз.`);
        } else {
            appendSystem(`Ошибка: ${error.message}`);
        }
        if (!fullText) {
            content.closest('.msg')?.remove();
        }
        setStatus('#ef4444');
    } finally {
        clearTimeout(timer);
        setBusy(false);
        userInput.focus();
        showWelcomeIfEmpty();
    }
}

/* ============================================================
   Загрузка файлов
   ============================================================ */
attachBtn.addEventListener('click', (e) => {
    e.preventDefault(); e.stopPropagation();
    if (busy) return;
    fileInput.click();
});

fileInput.addEventListener('change', () => {
    if (fileInput.files && fileInput.files.length > 0) uploadFiles();
});

chatContainer.addEventListener('dragover', (e) => {
    e.preventDefault(); e.stopPropagation();
    chatContainer.classList.add('drag-over');
});
chatContainer.addEventListener('dragleave', (e) => {
    e.preventDefault(); e.stopPropagation();
    chatContainer.classList.remove('drag-over');
});
chatContainer.addEventListener('drop', async (e) => {
    e.preventDefault(); e.stopPropagation();
    chatContainer.classList.remove('drag-over');
    if (busy) return;
    const dt = e.dataTransfer;
    if (!dt || !dt.files || dt.files.length === 0) return;
    try {
        const newDt = new DataTransfer();
        for (const f of dt.files) newDt.items.add(f);
        fileInput.files = newDt.files;
        await uploadFiles();
    } catch (err) {
        appendSystem(`Не удалось принять файлы: ${err.message}`);
    }
});

async function uploadFiles() {
    if (busy) return;
    const files = Array.from(fileInput.files || []);
    if (files.length === 0) return;

    appendUser(`Загружаю ${files.length} файл(ов): ${files.map(f => f.name).join(', ')}`);
    setBusy(true);
    setStatus('#f59e0b');

    const results = { models: [], documents: [], errors: [] };

    for (const file of files) {
        try {
            const formData = new FormData();
            formData.append('file', file, file.name);

            const ctrl = new AbortController();
            const timer = setTimeout(() => ctrl.abort(), UPLOAD_TIMEOUT_MS);
            let resp;
            try {
                resp = await fetch('/api/upload', {
                    method: 'POST', body: formData, signal: ctrl.signal
                });
            } finally {
                clearTimeout(timer);
            }

            if (!resp.ok) throw new Error(await readError(resp));
            const data = await resp.json();
            if (!data.filename) data.filename = file.name;

            if (data.type === 'model') results.models.push(data);
            else if (data.type === 'document') results.documents.push(data);
        } catch (err) {
            const msg = err.name === 'AbortError'
                ? 'таймаут загрузки (3 мин)'
                : err.message;
            results.errors.push({ name: file.name, error: msg });
        }
    }

    if (results.models.length) {
        const lines = results.models.map(m => {
            if (m.loaded) return `• ${m.filename} — загружена на стол`;
            return `• ${m.filename} — сохранена, но не загружена (${m.load_error || 'неизвестная ошибка'})`;
        }).join('\n');
        appendAIMessage(`**Модели (${results.models.length}):**\n${lines}`);
    }
    if (results.documents.length) {
        const lines = results.documents.map(d => {
            const chars = d.text_length || 0;
            if (chars > 0) return `• ${d.filename} — распознано ${chars} симв.`;
            if (d.text_error) return `• ${d.filename} — ⚠ ${d.text_error}`;
            return `• ${d.filename} — текст не извлечён`;
        }).join('\n');
        appendAIMessage(`**Документы (${results.documents.length}):**\n${lines}`);
    }
    if (results.errors.length) {
        const lines = results.errors.map(e => `• ${e.name} — ${e.error}`).join('\n');
        appendSystem(`Ошибки загрузки:\n${lines}`);
    }

    const firstDoc = results.documents.find(d => d.text_preview);
    if (firstDoc) {
        appendAIMessage(`**Фрагмент из «${firstDoc.filename}»:**\n${firstDoc.text_preview}`);
    }

    if (results.models.length || results.documents.length) {
        appendAIMessage('Опишите, что нужно сделать на основе загруженных данных.');
    }

    fileInput.value = '';
    setBusy(false);
    setStatus(results.errors.length ? '#ef4444' : '#10b981');
    userInput.focus();
}

/* ============================================================
   Очистка
   ============================================================ */
async function resetContext() {
    if (busy) return;
    setBusy(true);
    try {
        const resp = await fetch('/api/reset', { method: 'POST' });
        if (!resp.ok) throw new Error(await readError(resp));
        appendSystem('Контекст загруженных файлов очищен.');
        setTimeout(checkDependencies, 500);
    } catch (e) {
        appendSystem(`Не удалось очистить контекст: ${e.message}`);
    } finally {
        setBusy(false);
        userInput.focus();
    }
}

function clearChat() {
    if (!confirm('Очистить чат? История сообщений будет удалена (файлы на сервере останутся).')) return;
    messagesContainer.querySelectorAll('.msg').forEach(el => el.remove());
    showWelcomeIfEmpty();
}

/* ============================================================
   Кнопки и клавиши
   ============================================================ */
clearChatBtn.addEventListener('click', (e) => { e.preventDefault(); clearChat(); });
resetContextBtn.addEventListener('click', (e) => { e.preventDefault(); resetContext(); });
sendBtn.addEventListener('click', (e) => { e.preventDefault(); sendMessage(); });

userInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        if (!busy) sendMessage();
    }
});

/* ============================================================
   Глобальные ошибки
   ============================================================ */
window.addEventListener('unhandledrejection', (ev) => {
    console.error('Unhandled rejection:', ev.reason);
    appendSystem(`Необработанная ошибка: ${ev.reason?.message || ev.reason}`);
});

/* ============================================================
   Инициализация
   ============================================================ */
setStatus('#10b981');
checkDependencies();
autoGrow();
userInput.focus();