import { useState, useRef, useEffect } from 'react';
import { Send, Sparkles, User, Bot, AlertCircle } from 'lucide-react';
import { api } from '../../api/client';

type Message = {
  role: 'user' | 'assistant';
  content: string;
};

export function PublicChat() {
  const [messages, setMessages] = useState<Message[]>([
    { role: 'assistant', content: 'Hello! I am the Solaris Atlas lore assistant. Ask me any question about the story, characters, or world of Wuthering Waves!' }
  ]);
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || isLoading) return;

    const userMessage = input.trim();
    setInput('');
    setError(null);
    setMessages(prev => [...prev, { role: 'user', content: userMessage }]);
    setIsLoading(true);

    try {
      // Create the payload matching PublicChatRequest model
      const payload = {
        messages: messages.slice(1).concat({ role: 'user', content: userMessage }).map(m => ({ role: m.role, content: m.content }))
      };

      const response = await api<{ reply: string }>('/search/chat', {
        method: 'POST',
        body: JSON.stringify(payload)
      });
      
      if (response && response.reply) {
        setMessages(prev => [...prev, { role: 'assistant', content: response.reply }]);
      } else {
        throw new Error('Invalid response from server');
      }
    } catch (err: any) {
      console.error('Chat error:', err);
      // Handle rate limits and generic errors
      if (err.status === 429) {
        setError('You have reached the daily limit of 5 questions. Please come back tomorrow!');
      } else {
        setError(err.message || 'Something went wrong. Please try again.');
      }
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="public-chat-widget">
      <div className="chat-header">
        <Sparkles size={18} />
        <div>
          <h3>Ask the Lore Atlas</h3>
          <span>AI Assistant (Beta)</span>
        </div>
      </div>
      
      <div className="chat-messages">
        {messages.map((msg, i) => (
          <div key={i} className={`chat-message ${msg.role}`}>
            <div className="message-avatar">
              {msg.role === 'assistant' ? <Bot size={16} /> : <User size={16} />}
            </div>
            <div className="message-content">
              {/* Simple rendering for now. In a full implementation, markdown could be parsed here */}
              {msg.content.split('\n').map((line, j) => (
                <p key={j}>{line}</p>
              ))}
            </div>
          </div>
        ))}
        {isLoading && (
          <div className="chat-message assistant loading">
            <div className="message-avatar">
              <Bot size={16} />
            </div>
            <div className="message-content">
              <span className="dot-typing"></span>
            </div>
          </div>
        )}
        {error && (
          <div className="chat-error">
            <AlertCircle size={14} /> {error}
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      <form onSubmit={handleSubmit} className="chat-input-form">
        <input 
          type="text" 
          value={input}
          onChange={e => setInput(e.target.value)}
          placeholder="Ask a question about the lore..."
          disabled={isLoading}
        />
        <button type="submit" disabled={!input.trim() || isLoading} className="btn primary icon-only">
          <Send size={16} />
        </button>
      </form>
    </div>
  );
}
