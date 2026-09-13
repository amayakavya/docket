import { useState, useRef, useEffect } from "react";
import {
  BotMessageSquare, Send, Loader2, RotateCcw,
  ChevronRight, Copy, Check,
} from "lucide-react";
import { api } from "./api";

type Role = "user" | "assistant";
interface Message {
  role: Role;
  content: string;
  ts: string;
  suggestions?: string[];
}

// ─── Starter questions ────────────────────────────────────────────────────────
const STARTERS = [
  { label: "Platform status report", q: "Give me a full platform status report" },
  { label: "Critical cases", q: "List all critical active cases right now" },
  { label: "SLA health", q: "Which department has the most overdue SLA cases?" },
  { label: "Resolution rates", q: "Show me resolution rate by department" },
  { label: "Top complaint types", q: "What are the most common complaint types this week?" },
  { label: "Fraud desk summary", q: "Give me a detailed fraud desk summary" },
  { label: "Escalation status", q: "How many cases are escalated and why?" },
  { label: "Recent resolutions", q: "What were the last 5 cases resolved and how?" },
];

// ─── Simple markdown renderer ─────────────────────────────────────────────────
function renderMarkdown(text: string): React.ReactNode[] {
  // Split follow-up suggestions off the end first
  const followupSep = "**Follow-up suggestions:**";
  let main = text;
  let suggestions: string[] = [];
  const sepIdx = text.lastIndexOf(followupSep);
  if (sepIdx !== -1) {
    main = text.slice(0, sepIdx).trimEnd();
    const sugBlock = text.slice(sepIdx + followupSep.length).trim();
    suggestions = sugBlock
      .split("\n")
      .map(l => l.replace(/^[-*\d.]+\s*/, "").trim())
      .filter(Boolean);
  }

  const nodes: React.ReactNode[] = [];
  const lines = main.split("\n");
  let i = 0;
  let key = 0;

  function inlineRender(line: string): React.ReactNode {
    // Bold **text** and inline code `text`
    const parts: React.ReactNode[] = [];
    const rx = /(\*\*[^*]+\*\*|`[^`]+`)/g;
    let last = 0;
    let m: RegExpExecArray | null;
    while ((m = rx.exec(line)) !== null) {
      if (m.index > last) parts.push(line.slice(last, m.index));
      if (m[0].startsWith("**")) {
        parts.push(<strong key={key++}>{m[0].slice(2, -2)}</strong>);
      } else {
        parts.push(<code key={key++} className="aa-inline-code">{m[0].slice(1, -1)}</code>);
      }
      last = m.index + m[0].length;
    }
    if (last < line.length) parts.push(line.slice(last));
    return parts;
  }

  while (i < lines.length) {
    const line = lines[i];

    // H2 heading
    if (line.startsWith("## ")) {
      nodes.push(<h3 key={key++} className="aa-h2">{line.slice(3)}</h3>);
      i++; continue;
    }
    // H3 heading
    if (line.startsWith("### ")) {
      nodes.push(<p key={key++} className="aa-h3">{line.slice(4)}</p>);
      i++; continue;
    }
    // Bullet list — collect consecutive bullets
    if (/^[-*•]\s/.test(line)) {
      const items: React.ReactNode[] = [];
      while (i < lines.length && /^[-*•]\s/.test(lines[i])) {
        items.push(<li key={key++}>{inlineRender(lines[i].replace(/^[-*•]\s/, ""))}</li>);
        i++;
      }
      nodes.push(<ul key={key++} className="aa-ul">{items}</ul>);
      continue;
    }
    // Numbered list
    if (/^\d+\.\s/.test(line)) {
      const items: React.ReactNode[] = [];
      while (i < lines.length && /^\d+\.\s/.test(lines[i])) {
        items.push(<li key={key++}>{inlineRender(lines[i].replace(/^\d+\.\s/, ""))}</li>);
        i++;
      }
      nodes.push(<ol key={key++} className="aa-ol">{items}</ol>);
      continue;
    }
    // Blank line → spacer
    if (line.trim() === "") {
      nodes.push(<div key={key++} className="aa-spacer" />);
      i++; continue;
    }
    // Normal paragraph
    nodes.push(<p key={key++} className="aa-p">{inlineRender(line)}</p>);
    i++;
  }

  return nodes;
}

// Split suggestions out of the raw text for use as chips
function extractSuggestions(text: string): { clean: string; suggestions: string[] } {
  const sep = "**Follow-up suggestions:**";
  const idx = text.lastIndexOf(sep);
  if (idx === -1) return { clean: text, suggestions: [] };
  const clean = text.slice(0, idx).trimEnd();
  const block = text.slice(idx + sep.length).trim();
  const suggestions = block
    .split("\n")
    .map(l => l.replace(/^[-*\d.]+\s*/, "").trim())
    .filter(s => s.length > 4 && s.length < 120);
  return { clean, suggestions };
}

// ─── Rendered message bubble ──────────────────────────────────────────────────
function AssistantBubble({ content, onSend }: { content: string; onSend: (q: string) => void }) {
  const [copied, setCopied] = useState(false);
  const { clean, suggestions } = extractSuggestions(content);

  function copy() {
    navigator.clipboard.writeText(clean).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    });
  }

  const nodes = renderMarkdown(clean);

  return (
    <div className="aa-assistant-wrapper">
      <div className="aa-bubble aa-bubble-assistant">
        <div className="aa-md">{nodes}</div>
        <button className="aa-copy" onClick={copy} title="Copy response">
          {copied ? <Check size={11} /> : <Copy size={11} />}
        </button>
      </div>
      {suggestions.length > 0 && (
        <div className="aa-chips">
          {suggestions.slice(0, 3).map((s, i) => (
            <button key={i} className="aa-chip" onClick={() => onSend(s)}>
              {s} <ChevronRight size={10} style={{ opacity: 0.5, flexShrink: 0 }} />
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function formatTime(iso: string) {
  return new Date(iso).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" });
}

// ─── Main component ───────────────────────────────────────────────────────────
export default function AdminAssistant() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  async function send(text?: string) {
    const msg = (text ?? input).trim();
    if (!msg || loading) return;
    setInput("");
    setError(null);

    const userMsg: Message = { role: "user", content: msg, ts: new Date().toISOString() };
    const history = messages.map(m => ({ role: m.role, content: m.content }));
    setMessages(prev => [...prev, userMsg]);
    setLoading(true);

    try {
      const res = await api.adminAssistant(msg, history);
      setMessages(prev => [
        ...prev,
        { role: "assistant", content: res.reply, ts: new Date().toISOString() },
      ]);
    } catch (e: any) {
      setError(e?.message ?? "Request failed");
    } finally {
      setLoading(false);
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }

  function handleKey(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
  }

  function reset() { setMessages([]); setError(null); setInput(""); }

  const empty = messages.length === 0;

  return (
    <div className="admin-assistant">
      {/* Header */}
      <div className="aa-header">
        <BotMessageSquare size={16} />
        <span>ARIA</span>
        <span className="aa-model">gemma4 · live data</span>
        {!empty && (
          <button className="aa-reset" onClick={reset} title="Clear conversation">
            <RotateCcw size={13} />
          </button>
        )}
      </div>

      {/* Messages */}
      <div className="aa-body">
        {empty ? (
          <div className="aa-empty">
            <BotMessageSquare size={34} className="aa-empty-icon" />
            <p className="aa-empty-title">ARIA — Operations Intelligence</p>
            <p className="aa-empty-sub">Ask about cases, customers, SLA health, department performance, or request a full report.</p>
            <div className="aa-starters">
              {STARTERS.map(s => (
                <button key={s.q} className="aa-starter" onClick={() => send(s.q)}>
                  <span>{s.label}</span>
                  <ChevronRight size={11} style={{ opacity: 0.45, flexShrink: 0 }} />
                </button>
              ))}
            </div>
          </div>
        ) : (
          <>
            {messages.map((m, i) => (
              <div key={i} className={`aa-msg aa-msg-${m.role}`}>
                {m.role === "assistant" ? (
                  <AssistantBubble content={m.content} onSend={send} />
                ) : (
                  <div className="aa-bubble aa-bubble-user">
                    <p className="aa-p">{m.content}</p>
                  </div>
                )}
                <div className="aa-ts">{formatTime(m.ts)}</div>
              </div>
            ))}
            {loading && (
              <div className="aa-msg aa-msg-assistant">
                <div className="aa-bubble aa-bubble-assistant aa-thinking">
                  <Loader2 size={13} className="aa-spin" />
                  <span>Analysing platform data…</span>
                </div>
              </div>
            )}
            {error && <div className="aa-error">{error}</div>}
          </>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="aa-footer">
        <textarea
          ref={inputRef}
          className="aa-input"
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKey}
          placeholder="Ask about a case, department, customer, or request a report… (Enter to send)"
          rows={2}
          disabled={loading}
        />
        <button
          className="aa-send"
          onClick={() => send()}
          disabled={loading || !input.trim()}
          title="Send (Enter)"
        >
          {loading ? <Loader2 size={16} className="aa-spin" /> : <Send size={16} />}
        </button>
      </div>
    </div>
  );
}
