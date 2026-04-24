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
        src: './public/slyled-logo.svg',
        replacesTitle: false,
      },
      favicon: '/slyled/favicon.ico',
      defaultLocale: 'root',
      locales: {
        root: { label: 'English', lang: 'en' },
        fr: { label: 'Français', lang: 'fr' },
      },
      social: {
        github: 'https://github.com/SlyWombat/SlyLED',
      },
      editLink: {
        baseUrl: 'https://github.com/SlyWombat/SlyLED/edit/main/docs/src/',
      },
      lastUpdated: true,
      customCss: ['./src/styles/kinetic-prism.css'],
      sidebar: [
        {
          label: 'Start here',
          items: [
            { label: 'Overview', link: '/docs/en/01-getting-started/' },
            { label: 'Walkthrough', link: '/docs/en/02-walkthrough/' },
            { label: 'Platforms', link: '/docs/en/03-platform-guide/' },
          ],
        },
        {
          label: 'Design',
          autogenerate: { directory: 'docs/en' },
          collapsed: true,
        },
        {
          label: 'Calibration (deep dive)',
          items: [
            { label: 'Appendix A — Cameras',
              link: '/docs/en/appendix-a-camera-calibration/' },
            { label: 'Appendix B — Moving heads',
              link: '/docs/en/appendix-b-mover-calibration/' },
          ],
        },
        {
          label: 'Reference',
          items: [
            { label: 'Glossary', link: '/docs/en/20-glossary/' },
            { label: 'API', link: '/docs/en/19-api-reference/' },
          ],
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
