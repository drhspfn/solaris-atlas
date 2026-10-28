import type { Metadata } from 'next';
import { CookieSettingsButton } from '@/components/layout/CookieConsent';

export const metadata: Metadata = {
  title: 'Legal & privacy',
  description:
    'About Solaris Atlas, terms of use, copyright requests, privacy, cookies and contact information.',
  alternates: { canonical: '/legal' },
  openGraph: { title: 'Legal & privacy | Solaris Atlas', url: '/legal' },
};

const sections = [
  ['about', 'About this project'],
  ['terms', 'Using the archive'],
  ['copyright', 'Copyright & requests'],
  ['privacy', 'Privacy'],
  ['storage', 'Cookies & local storage'],
  ['ai', 'AI assistant'],
  ['contact', 'Contact'],
];

export default function LegalPage() {
  return (
    <article className="legal-page">
      <header className="legal-heading">
        <span className="eyebrow left">SOLARIS ATLAS / PROJECT INFORMATION</span>
        <h1>Legal & privacy</h1>
        <p>
          A clear account of who runs this archive, how to use it, and what happens to your data.
        </p>
        <p>
          Last updated <time dateTime="2026-10-28">28 October 2026</time>
        </p>
      </header>
      <div className="legal-layout">
        <nav className="legal-toc" aria-label="On this page">
          {sections.map(([id, title]) => (
            <a key={id} href={`#${id}`}>
              {title}
            </a>
          ))}
        </nav>
        <div className="legal-content">
          <section id="about">
            <h2>About this project</h2>
            <p>
              Solaris Atlas is an independent, unofficial fan project for exploring Wuthering Waves
              characters, dialogue, places, events and story connections. The website is operated by
              Yevhenii Verbytskyi, also known as drhspfn, under the project name Solaris Atlas. It
              is not operated by a registered company at this time.
            </p>
            <p>
              Solaris Atlas is not affiliated with, sponsored by, approved by or endorsed by KURO
              GAMES. Contabo supplies hosting infrastructure; it is not the operator of this
              website.
            </p>
            <p>
              The archive is free to use, and AI features have usage limits. We are preparing
              Google AdSense; advertising is not active yet. Before advertising is enabled, we will
              provide the required advertising choices and update this notice with the actual providers.
            </p>
          </section>
          <section id="terms">
            <h2>Using the archive</h2>
            <p>
              You may browse the archive and use its tools for lawful research, reference and
              discussion. An account is optional for public browsing. If you register, use an email
              address you control, keep your credentials private and contact us if you suspect
              unauthorised access. A Solaris Atlas account is separate from your game account: never
              provide your game password.
            </p>
            <p>
              Do not bypass access controls or usage limits, interfere with other visitors, submit
              malicious code, impersonate another person or use the service to infringe someone
              else’s rights. Reasonable protective restrictions may be applied to abusive traffic.
              API and MCP access follow the same rules and do not grant extra rights to game assets.
            </p>
            <h3>Accuracy, availability and spoilers</h3>
            <p>
              The archive contains story spoilers. Imported text, translations, media associations,
              historical dates and event schedules can be incomplete or incorrect. Historical text
              may be paired with media available in a different game version. Distinguish source
              dialogue from interpretations and AI-generated explanations; verify important
              information against the game and official announcements.
            </p>
            <p>
              Features and datasets may change or be temporarily unavailable. This is an
              informational fan resource, not an official game service or a promise of uninterrupted
              access. These notices do not exclude rights or liabilities that cannot lawfully be
              excluded.
            </p>
            <h3>External resources</h3>
            <p>
              Links to other websites are provided for reference. Those websites have their own
              operators and policies. Public access to an image, recording, transcript or API
              response does not itself grant permission to redistribute, sell, train models on or
              otherwise reuse that material.
            </p>
          </section>
          <section id="copyright">
            <h2>Copyright & requests</h2>
            <p>
              Wuthering Waves, its names, characters, artwork, music, voice recordings, video, maps
              and other game materials belong to KURO GAMES and the respective rights holders.
              Solaris Atlas claims no ownership of those materials. Any rights in original website
              code or original editorial contributions are separate from rights in the game content;
              consult the relevant repository or contributor terms before reusing them.
            </p>
            <p>
              We link to the{' '}
              <a
                href="https://wutheringwaves.kurogames.com/p/en/produce.html"
                target="_blank"
                rel="noreferrer"
              >
                official Wuthering Waves derivative-work guidelines
              </a>{' '}
              for reference. Those guidelines do not establish that every use by this archive or a
              visitor is authorised, and this notice is not a licence from KURO GAMES.
            </p>
            <h3>Report a rights concern</h3>
            <p>
              If you own rights in material displayed here, or are authorised to act for the owner,
              contact{' '}
              <a href="mailto:admin@solarisatlas.fun?subject=Copyright%20request">
                admin@solarisatlas.fun
              </a>{' '}
              with:
            </p>
            <ul>
              <li>Your name, contact email and relationship to the rights holder.</li>
              <li>A description of the protected work and a reference identifying the original.</li>
              <li>The exact Solaris Atlas page or media URLs you want reviewed.</li>
              <li>
                The concern, the action requested, and any evidence of your authority to make the
                request.
              </li>
            </ul>
            <p>
              Please do not send identity documents or other sensitive information unless needed for
              a specific verification step. We will review sufficiently detailed requests and may
              restrict or remove material while the matter is assessed. This contact process does
              not limit any statutory notice procedure or legal remedy.
            </p>
          </section>
          <section id="privacy">
            <h2>Privacy</h2>
            <p>
              The website operator named above is responsible for the personal data processed for
              Solaris Atlas. Privacy questions and requests can be sent to{' '}
              <a href="mailto:admin@solarisatlas.fun?subject=Privacy%20request">
                admin@solarisatlas.fun
              </a>
              . You do not need an account to browse the public archive.
            </p>
            <h3>What is processed and why</h3>
            <div className="legal-table-wrap">
              <table>
                <caption className="sr-only">Personal data used by Solaris Atlas</caption>
                <thead>
                  <tr>
                    <th scope="col">Data</th>
                    <th scope="col">Purpose</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td>Account email, nickname, password hash and account timestamps</td>
                    <td>
                      Account creation, sign-in, account management and security. Passwords are
                      stored as hashes, not readable passwords.
                    </td>
                  </tr>
                  <tr>
                    <td>
                      Session identifiers, IP address, browser information and sign-in timestamps
                    </td>
                    <td>Maintain your session, prevent abuse and investigate security issues.</td>
                  </tr>
                  <tr>
                    <td>Google identity and verified email, if you choose Google sign-in</td>
                    <td>
                      Authenticate or link the identity you select. The integration does not request
                      access to Gmail messages or Google Drive files.
                    </td>
                  </tr>
                  <tr>
                    <td>
                      Requested URLs, request times, IP addresses and technical error information
                    </td>
                    <td>
                      Deliver pages and media, apply rate limits, protect infrastructure and
                      diagnose failures.
                    </td>
                  </tr>
                  <tr>
                    <td>
                      AI prompts, the conversation context sent with them, and retrieved archive
                      excerpts
                    </td>
                    <td>
                      Generate a reply through the configured AI provider. See the AI section below.
                    </td>
                  </tr>
                  <tr>
                    <td>Messages you send to project contact addresses</td>
                    <td>
                      Answer support, privacy and rights requests and keep the context needed to
                      resolve them.
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p>
              Account features are used at your request. Technical processing supports delivery and
              security of the service. Where applicable, the legal basis depends on the processing:
              providing the service you request, legitimate interests in reliable and secure
              operation, compliance with legal obligations, or consent when required for optional
              processing. A request to use a feature is not blanket consent to unrelated advertising
              or marketing.
            </p>
            <h3>Service providers and disclosure</h3>
            <p>
              Contabo provides server hosting. Cloudflare provides delivery and security
              infrastructure, including media delivery. Google processes information when you choose
              its sign-in flow. We are preparing to use Google AdSense for advertising; Google and
              its advertising partners would process advertising information when that service is
              enabled. OpenAI is the current AI API provider. Email you send is processed
              by the email services used for the project’s contact addresses. These services may
              process information in countries other than your own.
            </p>
            <p>
              See{' '}
              <a href="https://contabo.com/en/legal/" target="_blank" rel="noreferrer">
                Contabo’s legal information
              </a>
              ,{' '}
              <a href="https://www.cloudflare.com/privacypolicy/" target="_blank" rel="noreferrer">
                Cloudflare’s privacy policy
              </a>
              ,{' '}
              <a href="https://policies.google.com/privacy" target="_blank" rel="noreferrer">
                Google’s privacy policy
              </a>{' '}
              and{' '}
              <a href="https://openai.com/enterprise-privacy/" target="_blank" rel="noreferrer">
                OpenAI’s business-data information
              </a>
              . Their own processing is subject to their applicable terms; those notices do not
              replace our responsibilities for this site. Information may also be disclosed where
              required by law or necessary to address a security incident or a rights claim.
            </p>
            <h3>Retention and security</h3>
            <p>
              Account records are retained to operate the account until deletion is requested or the
              account is otherwise removed, subject to records needed for security, disputes or
              legal obligations. Session records have expiry and revocation states; expiry ends
              access but does not necessarily immediately erase the stored record. Technical logs
              and support correspondence are kept as needed to investigate issues or resolve the
              relevant request. Backup copies and provider-side records may have separate retention
              schedules. We do not promise immediate erasure from every backup or external provider.
            </p>
            <p>
              Local preferences remain in your browser until you change or clear them. AI requests
              are processed as described below. The site does not currently offer a fixed,
              automatically enforced deletion period for every class of operational data; contact us
              for a retention or deletion review concerning your information.
            </p>
            <p>
              We use HTTPS, hashed passwords and server-side sessions. No online service can
              guarantee absolute security. Do not send passwords, payment details, identity
              documents or other sensitive personal data through search or the lore assistant.
            </p>
            <h3>Your choices and requests</h3>
            <p>
              Depending on the law applicable to you, you may have rights to access, correct, erase,
              restrict, object to or receive a copy of your personal data, withdraw consent where
              processing relies on it, and complain to the competent data-protection authority.
              Contact us from the account email where possible and describe the request. We may need
              proportionate verification before disclosing or changing account data. We will explain
              any applicable limitations rather than promising rights that do not apply in every
              jurisdiction.
            </p>
            <p>
              Public archive browsing does not require signing in or using AI. This site is intended
              for a general game audience and does not knowingly solicit personal information from
              children below the age at which they can lawfully use the relevant service. A parent
              or guardian with a concern may contact us for review.
            </p>
          </section>
          <section id="storage">
            <h2>Cookies & local storage</h2>
            <p>Cookies are small browser records; local storage is saved in your browser for a site.
              Necessary storage supports sign-in, security and the site functions you choose.</p>
            <div className="legal-table-wrap"><table>
              <caption className="sr-only">Cookies and local storage used by Solaris Atlas</caption>
              <thead><tr><th scope="col">Name</th><th scope="col">Purpose and duration</th></tr></thead>
              <tbody>
                <tr><td>solaris_session</td><td>Authentication session; normally up to 30 days, or until sign-out or revocation. The configured name and duration may differ.</td></tr>
                <tr><td>solaris_csrf</td><td>Protects account requests against cross-site forgery; normally up to one day.</td></tr>
                <tr><td>solaris_registration; solaris_pending_link</td><td>Short-lived Google sign-in completion or account-linking state; normally 15 and 10 minutes respectively, or until used.</td></tr>
                <tr><td>wuwa-locale; atlas-map-filters; solaris-atlas:map-progress:v1; solaris-atlas:narrative-preferences:v1; solaris-atlas:guest-player-title:v1</td><td>Local language, map choices and saved progress, narrative settings and guest player name. They remain until changed or browser storage is cleared.</td></tr>
                <tr><td>solaris-atlas:cookie-choice:v1</td><td>Stores your choice on this device until you change it or clear browser storage. It is not sent to our server.</td></tr>
              </tbody>
            </table></div>
            <p>Cloudflare may use security cookies to protect the service. Their names and durations
              depend on the protection applied. Blocking necessary storage can prevent account or site features from working.</p>
            <h3>Advertising choices</h3>
            <p>Google AdSense is being prepared, but its advertising script is not currently enabled.
              You can choose “Only necessary” or “Accept all” in <CookieSettingsButton />. Your choice is stored
              on this device and can be changed at any time. A certified consent management platform
              will request advertising consent where required before ads are enabled; the current
              site choice is not a Google TCF consent signal.</p>
            <p>When advertising is enabled, Google and other advertising partners may use cookies or
              similar storage and information about visits to this and other websites to show and
              measure ads, including personalised ads when permitted. Even non-personalised AdSense
              ads may use cookies for measurement and frequency limits. Limited advertising may use
              IP addresses, and Google’s programmatic limited-ads setting can use storage for invalid
              traffic detection. We do not currently load these ad technologies. The actual list of
              advertising partners and their purposes will be available in the certified consent
              manager when advertising is configured.</p>
            <p>Learn how Google uses data in <a href="https://policies.google.com/technologies/partner-sites" target="_blank" rel="noreferrer">Google’s partner sites notice</a>,
              manage Google ad personalisation in <a href="https://myadcenter.google.com/" target="_blank" rel="noreferrer">My Ad Center</a>,
              or visit <a href="https://www.aboutads.info/choices/" target="_blank" rel="noreferrer">Your Ad Choices</a> for participating third-party networks.
              Browser site settings can also remove existing cookies and local storage; clearing local
              map progress does not delete server-side account records.</p>
          </section>
          <section id="ai">
            <h2>AI assistant</h2>
            <p>
              The lore assistant generates answers using an external AI API and archive search. When
              you submit a question, the message and conversation context supplied by the page are
              sent to the website backend and the configured provider, currently OpenAI. The
              provider also receives archive excerpts needed to answer. Avoid including personal
              information in your questions.
            </p>
            <p>
              The current public chat keeps the visible conversation in the open page rather than
              providing a saved account chat history. Requests still pass through server and
              provider infrastructure; this does not mean they are anonymous or exempt from security
              logging and provider retention. Provider-side handling is governed by the applicable
              API agreement and settings.
            </p>
            <p>
              AI answers can be wrong, incomplete or invented. They are not official statements from
              KURO GAMES or professional advice. Consult the cited source material and official game
              announcements. Limits are applied to keep this free feature available; bypassing them
              is not permitted.
            </p>
          </section>
          <section id="contact">
            <h2>Contact</h2>
            <address>
              <strong>Solaris Atlas</strong>
              <br />
              Operated by Yevhenii Verbytskyi (drhspfn)
              <br />
              Website: <a href="https://solarisatlas.fun/">solarisatlas.fun</a>
              <br />
              General, privacy and copyright enquiries:{' '}
              <a href="mailto:admin@solarisatlas.fun">admin@solarisatlas.fun</a>
            </address>
            <p>
              These notices describe the service as it currently operates. If the operator, service
              providers, data practices or commercial features change, this page will be updated
              with a new date. A future company, subscription or advertising programme is not
              presented here as an existing service.
            </p>
          </section>
        </div>
      </div>
    </article>
  );
}
