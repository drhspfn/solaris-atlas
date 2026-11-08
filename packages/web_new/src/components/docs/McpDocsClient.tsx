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
  ["get_quest_profile", "Get quest details and story context."],
  ["get_quest_media", "Find a quest’s cutscene references and playback context."],
  ["get_character_profile", "Get a character’s profile, materials and story appearances."],
  ["get_character_archive", "Explore character artwork, progression and voice lines."],
  ["get_character_skills", "Get talents and their level-dependent values."],
  ["get_character_model", "Find the character’s published 3D model."],
  ["get_character_connections", "Search a character’s quest and character connections."],
  ["get_item_profile", "Get item details and acquisition information."],
  ["get_item_sellers", "Find NPC shops and item offers."],
  ["search_cutscenes", "Search the cutscene archive."],
  ["get_cutscene", "Get a cutscene’s public page, media and available descriptions."],
  ["visual_search", "Find visual identities using an image and confirmed references."],
  ["visual_identify", "Detect objects in an image and inspect candidate identities."],
  ["visual_list_identities", "Browse visual identities, game forms and their entity links."],
  ["visual_get_reference", "Inspect an approved visual reference and its source."],
];

export function McpDocsClient() {
  const [origin, setOrigin] = useState("");
  useEffect(() => {
    setOrigin(window.location.origin);
  }, []);
  const base = origin || "https://solarisatlas.fun";
  const mcpUrl = `${base}/api/mcp/`;
  const sseUrl = `${base}/api/mcp/sse`;
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
          <span>Streamable HTTP</span>
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
          Server endpoint <span>· Streamable HTTP</span>
        </label>
        <Input
          id="mcp-endpoint"
          value={mcpUrl}
          readOnly
          copyable
          copyLabel="Copy URL"
          spellCheck={false}
        />
        <p className="mcp-note">
          Streamable HTTP is recommended. For older clients, the legacy SSE
          endpoint is available below.
        </p>
        <details className="mcp-legacy">
          <summary>Legacy SSE endpoint</summary>
          <Input
            aria-label="Legacy SSE endpoint"
            value={sseUrl}
            readOnly
            copyable
            copyLabel="Copy URL"
          />
        </details>
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
          Choose a client and use the Streamable HTTP endpoint above. No
          authentication is required.
        </p>
        <div className="mcp-setup">
          <details open>
            <summary>Claude web &amp; desktop</summary>
            <ol>
              <li>
                Open <strong>Customize → Connectors</strong>, then choose{" "}
                <strong>+ Add → Add custom connector</strong>.
              </li>
              <li>
                Name it Solaris Atlas and enter the server endpoint above.
              </li>
              <li>
                Choose <strong>No sign in</strong>, add the connector, and
                enable it in the conversation’s connector menu.
              </li>
            </ol>
            <p>
              Team and Enterprise workspaces may require an owner to add the
              connector first.
            </p>
            <a
              href="https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp"
              target="_blank"
              rel="noreferrer"
            >
              Claude setup guide ↗
            </a>
          </details>
          <details>
            <summary>Codex locally · CLI, IDE and desktop</summary>
            <p>
              Run this command in your terminal, then verify with{" "}
              <code>codex mcp list</code>. Local Codex clients on the same host
              share MCP configuration.
            </p>
            <Input
              aria-label="Codex setup command"
              value={`codex mcp add solaris-atlas --url ${mcpUrl}`}
              readOnly
              copyable
              copyLabel="Copy"
            />
            <p>
              Alternatively, add the following to{" "}
              <code>~/.codex/config.toml</code>:
            </p>
            <pre>
              <code>{`[mcp_servers.solaris-atlas]\nurl = "${mcpUrl}"`}</code>
            </pre>
            <a
              href="https://developers.openai.com/learn/docs-mcp"
              target="_blank"
              rel="noreferrer"
            >
              Codex MCP documentation ↗
            </a>
          </details>
          <details>
            <summary>ChatGPT web &amp; Codex plugin</summary>
            <ol>
              <li>
                Open{" "}
                <a
                  href="https://chatgpt.com/plugins"
                  target="_blank"
                  rel="noreferrer"
                >
                  ChatGPT Plugins
                </a>
                , select the plus button and{" "}
                <strong>Add custom MCP server</strong>.
              </li>
              <li>
                Name it Solaris Atlas, enter the endpoint above, and choose{" "}
                <strong>No authentication</strong>.
              </li>
              <li>
                Review the prompt and create the plugin. Install it, then select
                it in the conversation using <strong>@</strong>.
              </li>
            </ol>
            <p>
              Availability depends on your account and workspace permissions. An
              administrator may need to allow custom MCP connectors. Local Codex
              configuration is separate from web plugins.
            </p>
            <a
              href="https://developers.openai.com/api/docs/guides/custom-mcp-server"
              target="_blank"
              rel="noreferrer"
            >
              OpenAI setup guide ↗
            </a>
          </details>
          <details>
            <summary>Claude Code</summary>
            <p>
              Run this command in your project, then check the connection with{" "}
              <code>claude mcp list</code> or <code>/mcp</code>.
            </p>
            <Input
              aria-label="Claude Code setup command"
              value={`claude mcp add --transport http solaris-atlas ${mcpUrl}`}
              readOnly
              copyable
              copyLabel="Copy"
            />
          </details>
        </div>
        <Link className="mcp-return" to="/">
          <Search size={16} /> Explore the archive <ArrowRight size={16} />
        </Link>
      </section>
    </main>
  );
}
