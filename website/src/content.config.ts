import { defineCollection } from 'astro:content';
import { docsLoader } from '@astrojs/starlight/loaders';
import { docsSchema } from '@astrojs/starlight/schema';

// Starlight content collection — `src/content/docs/` is what Astro
// renders. Sync is driven by tools/docs/build.py --format website,
// which copies docs/src/{en,fr}/ → src/content/docs/{en,fr}/ and
// rewrites relative links + front-matter headers as needed.

export const collections = {
  docs: defineCollection({ loader: docsLoader(), schema: docsSchema() }),
};
