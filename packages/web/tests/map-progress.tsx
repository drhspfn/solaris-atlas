import '../src/styles/index.css';

import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';

import { APP_SETTINGS } from '../src/config/settings';
import { WorldMapPage } from '../src/pages/WorldMapPage';

// Test controls operate only on this separate fixture origin, never game data.
const originalSetItem = Storage.prototype.setItem;
createRoot(document.getElementById('root')!).render(
  <BrowserRouter>
    <header style={{ height: 70, padding: 12, display: 'flex', alignItems: 'center', gap: 8 }}>
      <small>Illustrative map fixture</small>
      <button
        onClick={() => {
          Storage.prototype.setItem = function (key, value) {
            if (key === APP_SETTINGS.storage.mapProgress) throw new Error('QuotaExceeded');
            originalSetItem.call(this, key, value);
          };
        }}
      >
        Block progress storage
      </button>
      <button
        onClick={() => {
          Storage.prototype.setItem = originalSetItem;
        }}
      >
        Restore storage
      </button>
    </header>
    <WorldMapPage />
  </BrowserRouter>,
);
