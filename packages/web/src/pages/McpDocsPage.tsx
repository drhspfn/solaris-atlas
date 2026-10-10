import { Link } from 'react-router-dom';
import { Copy } from 'lucide-react';

export function McpDocsPage() {
  const sseUrl = `${window.location.origin}/api/mcp/sse`;

  const copyUrl = () => {
    navigator.clipboard.writeText(sseUrl);
    alert('Copied to clipboard');
  };

  return (
    <div className="page-container" style={{ maxWidth: '800px', margin: '0 auto', padding: '2rem 1rem' }}>
      <h1>Model Context Protocol (MCP) Integration</h1>
      <p>
        Solaris Atlas provides a public MCP server that allows you to connect compatible AI clients 
        (like Claude Desktop, Cursor, or your own agents) directly to our Wuthering Waves lore database.
      </p>

      <section style={{ marginTop: '2rem' }}>
        <h2>Connection Details</h2>
        <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', background: 'var(--surface-color)', padding: '1rem', borderRadius: '8px', marginTop: '1rem' }}>
          <code style={{ flex: 1, wordBreak: 'break-all' }}>{sseUrl}</code>
          <button onClick={copyUrl} className="btn primary" aria-label="Copy URL">
            <Copy size={16} /> Copy
          </button>
        </div>
        <p style={{ marginTop: '0.5rem', fontSize: '0.9rem', color: 'var(--text-secondary)' }}>
          This endpoint uses the standard Server-Sent Events (SSE) transport for MCP.
        </p>
      </section>

      <section style={{ marginTop: '2rem' }}>
        <h2>Available Tools</h2>
        <ul style={{ lineHeight: '1.8' }}>
          <li>
            <strong>search:</strong> 1:1 replica of the public <code>GET /api/search</code> endpoint. Search entities (characters, quests, items, locations) or spoken in-game dialogue lines (<code>scope="dialogue"</code>) across the entire game archive.
          </li>
          <li>
            <strong>get_node:</strong> Retrieve any knowledge graph node by its numeric ID or <code>canonical_key</code>, including type, title, slug, and metadata.
          </li>
          <li>
            <strong>get_node_related:</strong> Graph traversal tool (переход по нодам). 1:1 replica of <code>GET /api/nodes/&#123;canonical_key&#125;/related</code>. Traverses relations to neighboring nodes (e.g. quests where a character appears, connected locations, items).
          </li>
          <li>
            <strong>get_node_edges:</strong> 1:1 replica of <code>GET /api/nodes/&#123;canonical_key&#125;/edges</code>. Returns raw graph edges and relation types.
          </li>
          <li>
            <strong>get_node_narrative_context:</strong> 1:1 replica of <code>GET /api/nodes/&#123;canonical_key&#125;/narrative-context</code>. Resolves surrounding dialogue lines and owning quest for a dialogue node.
          </li>
          <li>
            <strong>get_quest_transcript:</strong> 1:1 replica of <code>GET /api/quests/&#123;id&#125;/transcript</code>. Full chronological dialogue transcript of a quest.
          </li>
          <li>
            <strong>search_lore:</strong> Search synthesized storyline explanations, cutscene analyses, and lore notes.
          </li>
          <li>
            <strong>get_character_timeline:</strong> Chronology of events and appearances for a character.
          </li>
        </ul>
      </section>

      <section style={{ marginTop: '2rem' }}>
        <h2>Connecting to Local Clients (Claude Desktop, Cursor)</h2>
        <p>Currently, desktop clients like Claude Desktop and Cursor primarily support <strong>stdio</strong> transports, while our public server uses <strong>SSE (Server-Sent Events) over HTTP</strong>.</p>
        <p>To connect, you will need a small proxy script that forwards stdio to our SSE endpoint. You can use the official MCP SSE-to-stdio bridge or write a simple Python/Node script to connect to <code>{sseUrl}</code>.</p>
      </section>
      
      <div style={{ marginTop: '3rem', textAlign: 'center' }}>
        <Link to="/" className="btn">Back to Home</Link>
      </div>
    </div>
  );
}
