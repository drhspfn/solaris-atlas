import '../src/styles/index.css';

import { createRoot } from 'react-dom/client';
import { BrowserRouter, Route, Routes } from 'react-router-dom';

import { QuestExplanation } from '../src/components/story/QuestExplanation';
import { StoryConnectionPage } from '../src/pages/StoryConnectionPage';

createRoot(document.getElementById('root')!).render(
  <BrowserRouter>
    <Routes>
      <Route
        path="/story-analysis/connections/:documentId/:index"
        element={<StoryConnectionPage />}
      />
      <Route
        path="*"
        element={<QuestExplanation questId={139000025} version="1.0.0" locale="en" />}
      />
    </Routes>
  </BrowserRouter>,
);
