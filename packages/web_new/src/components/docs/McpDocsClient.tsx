"use client";

import {
  ArrowRight,
  Braces,
  Cable,
  Database,
  Network,
  Search,
} from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "@/lib/router";
import { Input } from "@/components/ui/Input";
import "@/styles/mcp-docs.css";

const tools = [
  [
    "search",
    "Search characters, quests, items, locations and dialogue across the archive.",
  ],
  ["get_node", "Retrieve a source record by its numeric ID or canonical key."],
  [
    "get_node_related",
    "Explore connected quests, characters, locations and items.",
  ],
  ["get_node_edges", "Inspect graph edges and their relation types."],
  [
    "get_node_narrative_context",
    "Find the surrounding dialogue and owning quest for a line.",
  ],
  ["get_quest_transcript", "Read the authored dialogue transcript of a quest."],
  [
    "search_lore",
    "Search story explanations, cutscene analyses and lore notes.",
  ],
  [
    "get_character_timeline",
    "Explore a character’s recorded events and appearances.",
  ],
];

export function McpDocsClient() {
  const [origin, setOrigin] = useState("");
  useEffect(() => {
    setOrigin(window.location.origin);
  }, []);
  const sseUrl = origin ? `${origin}/api/mcp/sse` : "/api/mcp/sse";
  return (
    <main className="mcp-page page-container">
      <nav className="mcp-breadcrumb" aria-label="Breadcrumb">
        <Link to="/">Archive</Link>
        <span>/</span>
        <span>MCP</span>
      </nav>
      <header className="mcp-hero">
        <span className="mcp-eyebrow">SOLARIS ATLAS / DEVELOPER ACCESS</span>
        <div className="mcp-hero-heading">
          <div>
            <h1>
              Connect to the archive<span>.</span>
            </h1>
            <p>
              Bring Wuthering Waves stories, characters and source-linked
              dialogue into your AI tools with the Model Context Protocol.
            </p>
          </div>
          <div className="mcp-hero-mark" aria-hidden="true">
            <Network size={38} strokeWidth={1.2} />
          </div>
        </div>
        <div className="mcp-hero-tags">
          <span>
            <span className="mcp-dot" /> Public endpoint
          </span>
          <span>SSE transport</span>
          <span>Model Context Protocol</span>
        </div>
      </header>
      <section
        className="mcp-panel mcp-connect"
        aria-labelledby="connection-title"
      >
        <div className="mcp-section-heading">
          <span className="mcp-icon">
            <Cable size={20} />
          </span>
          <div>
            <span className="mcp-eyebrow">GET CONNECTED</span>
            <h2 id="connection-title">Connection details</h2>
          </div>
        </div>
        <p>
          Use this endpoint in an MCP-compatible client to connect to the
          Solaris Atlas archive.
        </p>
        <label htmlFor="mcp-endpoint">
          Server endpoint <span>· SSE</span>
        </label>
        <Input
          id="mcp-endpoint"
          value={sseUrl}
          readOnly
          copyable
          copyLabel="Copy URL"
          spellCheck={false}
        />
        <p className="mcp-note">
          The endpoint uses Server-Sent Events (SSE) over HTTP.
        </p>
      </section>
      <section className="mcp-tools" aria-labelledby="tools-title">
        <div className="mcp-section-heading">
          <span className="mcp-icon">
            <Braces size={20} />
          </span>
          <div>
            <span className="mcp-eyebrow">EXPLORE THE DATA</span>
            <h2 id="tools-title">Available tools</h2>
          </div>
        </div>
        <p>Search the archive and follow the connections behind each story.</p>
        <div className="mcp-tool-grid">
          {tools.map(([name, description]) => (
            <article className="mcp-tool" key={name}>
              <div className="mcp-tool-title">
                <code>{name}</code>
              </div>
              <p>{description}</p>
            </article>
          ))}
        </div>
      </section>
      <section
        className="mcp-panel mcp-clients"
        aria-labelledby="clients-title"
      >
        <div className="mcp-section-heading">
          <span className="mcp-icon">
            <Database size={20} />
          </span>
          <div>
            <span className="mcp-eyebrow">CLIENT SETUP</span>
            <h2 id="clients-title">Connect your client</h2>
          </div>
        </div>
        <p>
          Configure your MCP client to use the SSE endpoint above. If your
          client only supports stdio, use a compatible SSE-to-stdio bridge.
        </p>
        <Link className="mcp-return" to="/">
          <Search size={16} /> Explore the archive <ArrowRight size={16} />
        </Link>
      </section>
    </main>
  );
}
