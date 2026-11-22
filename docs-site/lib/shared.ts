import { createGetUrl } from 'fumadocs-core/source';

export const appName = 'StruggleTime';
export const docsRoute = '/docs';
export const docsImageRoute = '/og/docs';

export const gitConfig = {
  user: 'derekified',
  repo: 'struggletime',
  branch: 'main',
  host: 'github.com',
};

const getImageUrl = createGetUrl(docsImageRoute);

export function getPageImageUrl(page: { slugs: string[]; locale?: string }) {
  const segments = [...page.slugs, 'image.png'];

  return { segments, url: getImageUrl(segments, page.locale) };
}
