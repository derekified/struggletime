'use client';

import { useEffect } from 'react';

const LIGHT = new Set([
  'catppuccin-iced-latte',
  'catppuccin-latte',
  'github-light',
  'rose-pine-dawn',
  'paper',
]);

const ALIAS: Record<string, string> = {
  lime: 'neon',
  ember: 'classic',
  ocean: 'nord',
  forest: 'gruvbox-dark',
  ink: 'midnight',
  obsidian: 'amoled',
  paper: 'github-light',
  crt: 'neon',
  nordic: 'nord',
  sand: 'gruvbox-dark',
  'catppuccin-latte': 'catppuccin-iced-latte',
};

const KNOWN = new Set([
  'classic',
  'midnight',
  'neon',
  'catppuccin-mocha',
  'catppuccin-iced-latte',
  'gruvbox-dark',
  'github-dark',
  'github-light',
  'nord',
  'rose-pine',
  'rose-pine-dawn',
  'amoled',
]);

function resolve(raw: string | null): string {
  let v = (raw || '').trim() || 'classic';
  if (ALIAS[v]) v = ALIAS[v];
  if (!KNOWN.has(v)) v = 'classic';
  return v;
}

/** Apply SPA `st_theme` to docs so hamburger/foreground match light vs dark. */
export function DocsThemeSync() {
  useEffect(() => {
    const apply = () => {
      const theme = resolve(
        typeof localStorage !== 'undefined'
          ? localStorage.getItem('st_theme')
          : 'classic',
      );
      const root = document.documentElement;
      root.setAttribute('data-theme', theme);
      const light = LIGHT.has(theme);
      root.classList.toggle('dark', !light);
      root.style.colorScheme = light ? 'light' : 'dark';
    };
    apply();
    window.addEventListener('storage', apply);
    return () => window.removeEventListener('storage', apply);
  }, []);
  return null;
}
