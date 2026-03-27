import { useState, useRef, useEffect, useCallback } from 'react';
import { Send, Bot, User, Loader2, Trash2, Zap, Paperclip, X, FileText, Image } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import { useAuth } from '../../contexts/AuthContext';
import { api } from '../../api/client';
import ClawForgeLogo from '../../components/ClawForgeLogo';

interface Message {
  id: number;
  role: 'user' | 'assistant';
  content: string;
  timestamp: string;
  source?: string;
  model?: string;
}

interface Attachment {
  name: string;
  size: number;
  type: string;
  isText: boolean;
  contentPreview?: string;
}

const STORAGE_KEY = 'openclaw_portal_chat';
const WARM_KEY = 'openclaw_agent_connected';

function loadMessages(userId: string): Message[] {
  try {
    const raw = localStorage.getItem(`${STORAGE_KEY}_${userId}`);
    return raw ? JSON.parse(raw) : [];
  } catch { return []; }
}
function saveMessages(userId: string, messages: Message[]) {
  localStorage.setItem(`${STORAGE_KEY}_${userId}`, JSON.stringify(messages));
}
function isAgentWarm(userId: string): boolean {
  return localStorage.getItem(`${WARM_KEY}_${userId}`) === 'true';
}
function markAgentWarm(userId: string) {
  localStorage.setItem(`${WARM_KEY}_${userId}`, 'true');
}

function fmtSize(b: number) {
  if (b > 1e6) return `${(b / 1e6).toFixed(1)} MB`;
  if (b > 1e3) return `${(b / 1e3).toFixed(0)} KB`;
  return `${b} B`;
}

// ── Warmup indicator — only shown on first-ever connection ──────────────────

function WarmupIndicator() {
  // Wait 1s before showing (steal 1 second from the perceived wait)
  const [visible, setVisible] = useState(false);
  const [remaining, setRemaining] = useState(6);

  useEffect(() => {
    const show = setTimeout(() => setVisible(true), 1000);
    return () => clearTimeout(show);
  }, []);

  useEffect(() => {
    if (!visible || remaining <= 0) return;
    const t = setInterval(() => setRemaining(r => Math.max(0, r - 1)), 1000);
    return () => clearInterval(t);
  }, [visible, remaining]);

  if (!visible) {
    // First second: just show spinning indicator
    return (
      <div className="rounded-xl bg-dark-card border border-dark-border px-4 py-3 flex items-center gap-2">
        <Loader2 size={13} className="animate-spin text-text-muted" />
        <span className="text-xs text-text-muted">Thinking...</span>
      </div>
    );
  }

  const total = 6;
  const pct = Math.round(((total - remaining) / total) * 100);

  return (
    <div className="rounded-xl bg-dark-card border border-warning/30 px-4 py-3 w-72 space-y-2">
      <div className="flex items-center justify-between">
        <span className="flex items-center gap-1.5 text-xs text-warning font-medium">
          <Zap size={12} /> Agent starting up
        </span>
        <span className="text-xs text-text-muted tabular-nums">{remaining}s</span>
      </div>
      <div className="h-1 w-full rounded-full bg-dark-border overflow-hidden">
        <div
          className="h-full rounded-full bg-warning transition-[width] duration-1000"
          style={{ width: `${pct}%` }}
        />
      </div>
      <p className="text-[10px] text-text-muted">
        First message · cold-start takes ~10s — subsequent responses are instant
      </p>
    </div>
  );
}

// ── Main Chat ───────────────────────────────────────────────────────────────

export default function PortalChat() {
  const { user } = useAuth();
  const userId = user?.id || 'unknown';

  const [messages, setMessages] = useState<Message[]>(() => {
    const saved = loadMessages(userId);
    if (saved.length > 0) return saved;
    return [{
      id: 0, role: 'assistant',
      content: `Hello ${user?.name || 'there'}! I'm your **${user?.positionName || 'AI'} Agent** at ACME Corp.\n\nI can help you with tasks related to your ${user?.positionName || ''} role in the ${user?.departmentName || ''} department. Just type your question or request below.`,
      timestamp: new Date().toISOString(),
    }];
  });

  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [warm, setWarm] = useState(() => isAgentWarm(userId));
  const [attachment, setAttachment] = useState<Attachment | null>(null);
  const [uploading, setUploading] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => { saveMessages(userId, messages); }, [messages, userId]);
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages]);

  const clearChat = useCallback(() => {
    setMessages([{ id: Date.now(), role: 'assistant', content: 'Chat cleared. How can I help you?', timestamp: new Date().toISOString() }]);
  }, []);

  // ── File upload ────────────────────────────────────────────────────────────

  const handleFileSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    try {
      const form = new FormData();
      form.append('file', file);
      // Use fetch directly — Content-Type must NOT be set for multipart (browser sets boundary)
      const token = (window as any).__openclaw_token || localStorage.getItem('openclaw_token') || '';
      const res = await fetch('/api/v1/portal/upload', {
        method: 'POST',
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        body: form,
      });
      if (res.ok) {
        const data = await res.json();
        setAttachment({
          name: data.filename,
          size: data.size,
          type: data.type,
          isText: data.isText,
          contentPreview: data.contentPreview,
        });
      } else {
        // Upload failed — still attach locally with file info only
        setAttachment({ name: file.name, size: file.size, type: file.type, isText: false });
      }
    } catch {
      setAttachment({ name: file.name, size: file.size, type: file.type, isText: false });
    }
    setUploading(false);
    // Reset input so same file can be picked again
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  // ── Send message ───────────────────────────────────────────────────────────

  const sendMessage = async () => {
    const text = input.trim();
    if ((!text && !attachment) || sending) return;

    // Build the message content — attach file content inline
    let fullContent = text;
    if (attachment) {
      if (attachment.isText && attachment.contentPreview) {
        const ext = attachment.name.split('.').pop() || '';
        // Explicitly tell the agent the content is inline — prevents it from looking for a file path
        fullContent += `\n\nI'm sharing the following file content with you directly in this message. The content is complete and ready for you to read and analyze — no need to access any file system or S3.\n\n**File: ${attachment.name}** (${fmtSize(attachment.size)})\n\`\`\`${ext}\n${attachment.contentPreview}\n\`\`\``;
      } else {
        fullContent += `\n\n[Shared file: **${attachment.name}**, ${fmtSize(attachment.size)}, type: ${attachment.type}. Text content not available for this file type.]`;
      }
    }

    const userMsg: Message = { id: Date.now(), role: 'user', content: fullContent, timestamp: new Date().toISOString() };
    setMessages(prev => [...prev, userMsg]);
    setInput('');
    setAttachment(null);
    setSending(true);

    const doCall = () => api.post<{ response: string; source?: string; model?: string }>('/portal/chat', { message: fullContent });

    try {
      const resp = await doCall();
      setMessages(prev => [...prev, {
        id: Date.now() + 1, role: 'assistant', content: resp.response,
        timestamp: new Date().toISOString(), source: resp.source, model: resp.model,
      }]);
      // Mark agent as warm after first successful response
      if (!warm) { setWarm(true); markAgentWarm(userId); }
    } catch (e: any) {
      // 404 = no binding
      if (e?.status === 404 || String(e?.message || '').includes('No agent bound')) {
        setMessages(prev => [...prev, {
          id: Date.now() + 1, role: 'assistant',
          content: 'Your agent is not yet configured. Please contact your IT Admin.',
          timestamp: new Date().toISOString(), source: 'error',
        }]);
        setSending(false);
        return;
      }
      // Timeout — retry once (no warmup message — indicator already showing)
      try {
        await new Promise(r => setTimeout(r, 4000));
        const retry = await doCall();
        setMessages(prev => [...prev, {
          id: Date.now() + 2, role: 'assistant', content: retry.response,
          timestamp: new Date().toISOString(), source: retry.source, model: retry.model,
        }]);
        if (!warm) { setWarm(true); markAgentWarm(userId); }
      } catch {
        setMessages(prev => [...prev, {
          id: Date.now() + 2, role: 'assistant',
          content: 'Agent is still starting up. Please wait a moment and try again.',
          timestamp: new Date().toISOString(), source: 'error',
        }]);
      }
    } finally {
      setSending(false);
    }
  };

  const isImage = (type: string) => type.startsWith('image/');

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-dark-border px-6 py-3">
        <div className="flex items-center gap-3">
          <ClawForgeLogo size={36} animate={sending ? 'working' : 'idle'} />
          <div>
            <h1 className="text-sm font-semibold text-text-primary">{user?.positionName} Agent</h1>
            <p className="text-xs text-text-muted">{user?.name} · {user?.departmentName}</p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1.5">
            <div className={`w-2 h-2 rounded-full ${warm ? 'bg-success' : 'bg-warning'} animate-pulse`} />
            <span className={`text-xs ${warm ? 'text-success' : 'text-warning'}`}>
              {warm ? 'Connected' : 'Standby'}
            </span>
          </div>
          <button onClick={clearChat}
            className="flex items-center gap-1 rounded-lg px-2 py-1 text-xs text-text-muted hover:text-red-400 hover:bg-red-500/10 transition-colors"
            title="Clear display only — agent memory is preserved">
            <Trash2 size={14} /> Clear display
          </button>
        </div>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
        {messages.map(msg => (
          <div key={msg.id} className={`flex gap-3 ${msg.role === 'user' ? 'justify-end' : ''}`}>
            {msg.role === 'assistant' && (
              <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary mt-1">
                <Bot size={16} />
              </div>
            )}
            <div className={`max-w-[75%] rounded-xl px-4 py-3 ${
              msg.role === 'user'
                ? 'bg-primary text-white'
                : 'bg-dark-card border border-dark-border text-text-primary'
            }`}>
              {msg.role === 'assistant' ? (
                <div className="text-sm prose prose-invert prose-sm max-w-none
                  [&_p]:my-1 [&_h1]:text-base [&_h1]:font-bold [&_h1]:mt-3 [&_h1]:mb-1
                  [&_h2]:text-sm [&_h2]:font-semibold [&_h2]:mt-2 [&_h2]:mb-1
                  [&_h3]:text-sm [&_h3]:font-medium [&_h3]:mt-2
                  [&_ul]:my-1 [&_ol]:my-1 [&_li]:my-0.5
                  [&_code]:bg-dark-bg [&_code]:px-1 [&_code]:rounded
                  [&_pre]:bg-dark-bg [&_pre]:p-3 [&_pre]:rounded-lg [&_pre]:my-2 [&_pre]:overflow-x-auto
                  [&_table]:text-xs [&_th]:px-2 [&_th]:py-1 [&_td]:px-2 [&_td]:py-1
                  [&_strong]:text-text-primary [&_a]:text-primary-light [&_hr]:border-dark-border">
                  <ReactMarkdown>{msg.content}</ReactMarkdown>
                </div>
              ) : (
                <p className="text-sm whitespace-pre-wrap">{msg.content}</p>
              )}
              <p className={`text-[10px] mt-1.5 ${msg.role === 'user' ? 'text-white/60' : 'text-text-muted'}`}>
                {msg.role === 'user' && '✓ '}
                {new Date(msg.timestamp).toLocaleTimeString()}
                {msg.source === 'agentcore' && ' · AgentCore'}
                {msg.model && ` · ${msg.model.split('/').pop()?.split(':')[0] || ''}`}
              </p>
            </div>
            {msg.role === 'user' && (
              <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-blue-500/10 text-blue-400 mt-1">
                <User size={16} />
              </div>
            )}
          </div>
        ))}

        {/* Sending indicator */}
        {sending && (
          <div className="flex gap-3">
            <div className="shrink-0 mt-1"><ClawForgeLogo size={28} animate="working" /></div>
            {/* Only show warmup countdown on first-ever connection */}
            {!warm ? <WarmupIndicator /> : (
              <div className="rounded-xl bg-dark-card border border-dark-border px-4 py-3 flex items-center gap-2">
                <Loader2 size={13} className="animate-spin text-text-muted" />
                <span className="text-xs text-text-muted">Thinking...</span>
              </div>
            )}
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Attachment preview */}
      {attachment && (
        <div className="px-6 pt-2">
          <div className="flex items-center gap-2 rounded-xl bg-primary/10 border border-primary/30 px-3 py-2 w-fit max-w-xs">
            {isImage(attachment.type)
              ? <Image size={14} className="text-primary shrink-0" />
              : <FileText size={14} className="text-primary shrink-0" />}
            <span className="text-xs font-medium text-text-primary truncate">{attachment.name}</span>
            <span className="text-[10px] text-text-muted shrink-0">{fmtSize(attachment.size)}</span>
            {attachment.isText && <span className="text-[10px] text-success shrink-0">content ready</span>}
            <button onClick={() => setAttachment(null)} className="text-text-muted hover:text-danger ml-1 shrink-0">
              <X size={12} />
            </button>
          </div>
        </div>
      )}

      {/* Input */}
      <div className="border-t border-dark-border px-6 py-4">
        <div className="flex gap-2">
          {/* File upload button */}
          <input
            ref={fileInputRef}
            type="file"
            className="hidden"
            onChange={handleFileSelect}
          />
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={sending || uploading}
            className="flex h-12 w-12 items-center justify-center rounded-xl border border-dark-border bg-dark-bg text-text-muted hover:text-primary hover:border-primary/50 disabled:opacity-50 transition-colors shrink-0"
            title="Attach a file"
          >
            {uploading
              ? <Loader2 size={18} className="animate-spin" />
              : <Paperclip size={18} />}
          </button>

          <input
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && !e.shiftKey && sendMessage()}
            placeholder={attachment ? `Add a message about ${attachment.name}...` : 'Type your message...'}
            disabled={sending}
            className="flex-1 rounded-xl border border-dark-border bg-dark-bg px-4 py-3 text-sm text-text-primary placeholder:text-text-muted focus:border-primary focus:outline-none disabled:opacity-50"
          />
          <button
            onClick={sendMessage}
            disabled={(!input.trim() && !attachment) || sending}
            className="flex h-12 w-12 items-center justify-center rounded-xl bg-primary text-white hover:bg-primary/90 disabled:opacity-50 transition-colors shrink-0"
          >
            <Send size={18} />
          </button>
        </div>
        <div className="flex items-center justify-between mt-2">
          <p className="text-[10px] text-text-muted">
            Press Enter to send · Attach any file with the paperclip
          </p>
          <p className="text-[10px] text-text-muted">Powered by AWS Bedrock via AgentCore</p>
        </div>
      </div>
    </div>
  );
}
