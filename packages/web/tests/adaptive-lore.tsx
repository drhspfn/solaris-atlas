import '../src/styles/index.css';
import '../src/styles/story-agent.css';

import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';

import { AgentRevisits } from '../src/components/story/AgentRevisits';
import { QuestExplanation } from '../src/components/story/QuestExplanation';

createRoot(document.getElementById('root')!).render(
  <BrowserRouter>
    <main style={{ maxWidth: 1120, margin: '40px auto', padding: 20 }}>
      <p>Illustrative UI fixture · no game facts or paid calls</p>
      <QuestExplanation questId={139000025} version="1.0.0" locale="en" />
      <section className="agent-page">
        <AgentRevisits refresh={0} />
      </section>
    </main>
  </BrowserRouter>,
);
