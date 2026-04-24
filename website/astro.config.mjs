// @ts-check
import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';

// SlyLED public site.
//
// Content source of truth: ../docs/src/{en,fr}/. A pre-build step
// (tools/docs/build.py --format website) copies markdown into
// src/content/docs/{en,fr}/ so both languages stay filename-parallel.
// The marketing surface (../docs/src/marketing/*) lands under
// src/content/marketing/.

export default defineConfig({
  site: 'https://electricrv.ca',
  base: '/slyled',
  outDir: './dist',
  integrations: [
    starlight({
      title: 'SlyLED',
      description:
        'Open, three-tier stage-lighting control with local-first AI calibration.',
      logo: {
        src: './public/slyled-logo.png',
        replacesTitle: false,
      },
      defaultLocale: 'root',
      locales: {
        root: { label: 'English', lang: 'en' },
        fr: { label: 'Français', lang: 'fr' },
      },
      social: [
        { icon: 'github', label: 'GitHub',
          href: 'https://github.com/SlyWombat/SlyLED' },
      ],
      // No editLink — SlyLED docs are read-only to the public; contributors
      // go through the GitHub repo directly, not through per-page "edit"
      // shortcuts on the live site.
      lastUpdated: true,
      customCss: ['./src/styles/kinetic-prism.css'],
      // Starlight `defaultLocale: 'root'` puts EN at src/content/docs/<slug>.md,
      // so sidebar links resolve at the site root (no /docs/en/ prefix). The
      // `base: '/slyled'` option prefixes the final URL; DO NOT include it here.
      sidebar: [
        {
          label: 'Start here',
          items: [
            { label: 'Overview', slug: '01-getting-started' },
            { label: 'Walkthrough', slug: '02-walkthrough' },
            { label: 'Platforms', slug: '03-platform-guide' },
          ],
        },
        {
          label: 'Design',
          items: [
            { label: 'Fixture setup', slug: '04-fixture-setup' },
            { label: 'Stage layout', slug: '05-stage-layout' },
            { label: 'Stage objects', slug: '06-stage-objects' },
            { label: 'Spatial effects', slug: '07-spatial-effects' },
            { label: 'Track actions', slug: '08-track-actions' },
            { label: 'Building a timeline', slug: '09-building-timeline' },
            { label: 'Baking + playback', slug: '10-baking-playback' },
            { label: 'Show preview', slug: '11-show-preview' },
            { label: 'DMX profiles', slug: '12-dmx-profiles' },
            { label: 'Preset shows', slug: '13-preset-shows' },
          ],
          collapsed: true,
        },
        {
          label: 'Operations',
          items: [
            { label: 'Camera nodes', slug: '14-camera-nodes' },
            { label: 'Firmware + OTA', slug: '15-firmware-ota' },
            { label: 'System limits', slug: '16-system-limits' },
            { label: 'Troubleshooting', slug: '17-troubleshooting' },
            { label: 'Examples', slug: '18-examples' },
          ],
          collapsed: true,
        },
        {
          label: 'Calibration (deep dive)',
          items: [
            { label: 'Appendix A — Cameras', slug: 'appendix-a-camera-calibration' },
            { label: 'Appendix B — Moving heads', slug: 'appendix-b-mover-calibration' },
            { label: 'Appendix C — Maintenance', slug: 'appendix-c-maintenance' },
          ],
        },
        {
          label: 'Reference',
          items: [
            { label: 'Glossary', slug: '20-glossary' },
            { label: 'API', slug: '19-api-reference' },
          ],
        },
        {
          label: 'About',
          items: [
            { label: 'Feature overview', slug: 'marketing/hero' },
            { label: 'PLASA 2026 submission', slug: 'marketing/plasa-2026' },
          ],
          collapsed: true,
        },
        {
          label: 'Features',
          autogenerate: { directory: 'marketing/features' },
          collapsed: true,
        },
        {
          label: 'Case studies',
          autogenerate: { directory: 'marketing/case-studies' },
          collapsed: true,
        },
        {
          label: 'Press kit',
          autogenerate: { directory: 'marketing/press-kit' },
          collapsed: true,
        },
      ],
      head: [
        // Preconnect for Google Fonts (Space Grotesk + JetBrains Mono —
        // matches the Kinetic-Prism theme in the app + PDF).
        {
          tag: 'link',
          attrs: { rel: 'preconnect', href: 'https://fonts.gstatic.com', crossorigin: '' },
        },
      ],
    }),
  ],
});
