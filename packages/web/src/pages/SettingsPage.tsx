import { ChevronRight, Palette, Sparkles, UserRound } from 'lucide-react';
import { Link } from 'react-router-dom';

import { useAuth } from '../auth/AuthProvider';
import { PlayerText } from '../components/dialogue/PlayerText';
import { ROVER_TITLES } from '../data/playerName';
import { usePlayerDisplay } from '../hooks/usePlayerDisplay';
import { useNarrativePreferences } from '../preferences/NarrativePreferences';

export function SettingsPage() {
  const { user } = useAuth();
  const playerDisplay = usePlayerDisplay();
  const {
    nameMode,
    colorMode,
    customColor,
    preferredRover,
    setPreferredRover,
    setNameMode,
    setColorMode,
    setCustomColor,
  } = useNarrativePreferences();

  return (
    <div className="page-container settings-page">
      <div className="breadcrumbs">
        <Link to="/">Archive</Link>
        <ChevronRight size={13} />
        <span>Settings</span>
      </div>
      <header className="page-heading settings-heading">
        <div>
          <span className="eyebrow left">YOUR READING EXPERIENCE</span>
          <h1>
            Settings<span className="heading-period">.</span>
          </h1>
          <p>Choose how the Rover appears in dialogue across the archive.</p>
        </div>
      </header>

      <div className="settings-grid">
        <section className="content-panel settings-panel" aria-labelledby="rover-setting-title">
          <div className="panel-title">
            <span>
              <UserRound size={15} />
            </span>
            <h2 id="rover-setting-title">Rover in cutscenes</h2>
          </div>
          <p className="settings-help">
            Play your preferred Rover automatically when a cutscene has confirmed male and female
            versions. Story choices still ask for your answer.
          </p>
          <div className="settings-options" role="radiogroup" aria-labelledby="rover-setting-title">
            {(
              [
                ['ask', 'Ask each time', 'Choose a Rover when the versions diverge'],
                ['male', 'Male Rover', 'Continue with the male version'],
                ['female', 'Female Rover', 'Continue with the female version'],
              ] as const
            ).map(([value, label, description]) => (
              <label
                key={value}
                className={`settings-option ${preferredRover === value ? 'selected' : ''}`}
              >
                <input
                  type="radio"
                  name="preferred-rover"
                  value={value}
                  checked={preferredRover === value}
                  onChange={() => setPreferredRover(value)}
                />
                <span>
                  <strong>{label}</strong>
                  <small>{description}</small>
                </span>
              </label>
            ))}
          </div>
          <p className="settings-help">Saved automatically in this browser.</p>
        </section>
        <section className="content-panel settings-panel" aria-labelledby="name-setting-title">
          <div className="panel-title">
            <span>
              <UserRound size={15} />
            </span>
            <h2 id="name-setting-title">Name in dialogue</h2>
          </div>
          <p className="settings-help">
            This replaces the game’s <code>{'{PlayerName}'}</code> placeholder in transcript text.
          </p>
          <div className="settings-options" role="radiogroup" aria-labelledby="name-setting-title">
            <label className={`settings-option ${nameMode === 'nickname' ? 'selected' : ''}`}>
              <input
                type="radio"
                name="player-name"
                value="nickname"
                checked={nameMode === 'nickname'}
                onChange={() => setNameMode('nickname')}
              />
              <span>
                <strong>My nickname</strong>
                <small>
                  {user
                    ? `Use ${user.nickname} from your account`
                    : 'For signed-out visitors, use a stable Rover title'}
                </small>
              </span>
            </label>
            <label className={`settings-option ${nameMode === 'rover_title' ? 'selected' : ''}`}>
              <input
                type="radio"
                name="player-name"
                value="rover_title"
                checked={nameMode === 'rover_title'}
                onChange={() => setNameMode('rover_title')}
              />
              <span>
                <strong>Rover title</strong>
                <small>Use one title consistently on this browser</small>
              </span>
            </label>
          </div>
          <div className="settings-preview" aria-live="polite">
            <small>PREVIEW</small>
            <p>
              <PlayerText
                display={playerDisplay}
                value="{PlayerName}, would you be willing to participate in our preliminary tests?"
              />
            </p>
            <span>
              Current name: <b>{playerDisplay.name}</b>
            </span>
          </div>
          <details className="rover-title-list">
            <summary>
              Titles in the rotation <span>{ROVER_TITLES.length}</span>
            </summary>
            <div>
              {ROVER_TITLES.map((title) => (
                <span key={title}>{title}</span>
              ))}
            </div>
          </details>
        </section>

        <section className="content-panel settings-panel" aria-labelledby="color-setting-title">
          <div className="panel-title">
            <span>
              <Palette size={15} />
            </span>
            <h2 id="color-setting-title">Name color</h2>
          </div>
          <p className="settings-help">
            The chosen color is applied only to the inserted name, not the whole line.
          </p>
          <div className="settings-options" role="radiogroup" aria-labelledby="color-setting-title">
            <label className={`settings-option ${colorMode === 'accent' ? 'selected' : ''}`}>
              <input
                type="radio"
                name="player-color"
                value="accent"
                checked={colorMode === 'accent'}
                onChange={() => setColorMode('accent')}
              />
              <span>
                <strong>Atlas accent</strong>
                <small>Use the site’s mint highlight</small>
              </span>
              <i className="settings-color-swatch accent-swatch" aria-hidden="true" />
            </label>
            <div className={`settings-option ${colorMode === 'custom' ? 'selected' : ''}`}>
              <label className="settings-option-main">
                <input
                  type="radio"
                  name="player-color"
                  value="custom"
                  checked={colorMode === 'custom'}
                  onChange={() => setColorMode('custom')}
                />
                <span>
                  <strong>Custom color</strong>
                  <small>Choose a color for your name</small>
                </span>
              </label>
              <input
                className="settings-color-input"
                type="color"
                aria-label="Custom dialogue name color"
                value={customColor}
                onChange={(event) => setCustomColor(event.target.value)}
              />
            </div>
          </div>
          <div className="settings-preview color-preview" aria-live="polite">
            <small>
              <Sparkles size={12} /> IN A STORY LINE
            </small>
            <p>
              <PlayerText
                display={playerDisplay}
                value="Anyway, let’s call it a day. See you tomorrow, {PlayerName}."
              />
            </p>
          </div>
          <p className="settings-storage-note">
            These preferences are saved in this browser and update immediately.
          </p>
        </section>
      </div>
    </div>
  );
}
