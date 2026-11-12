import { createContext, useContext, useEffect, useMemo, useState } from 'react';

type Theme = 'dark' | 'light' | 'system';
type ResolvedTheme = Exclude<Theme, 'system'>;

const DEFAULT_THEME: Theme = 'system';
const STORAGE_KEY = 'quartz-theme';

type ThemeState = {
  theme: Theme;
  resolvedTheme: ResolvedTheme;
  setTheme: (theme: Theme) => void;
};

const ThemeContext = createContext<ThemeState | null>(null);

function read(): Theme {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if(stored === 'light' || stored === 'dark' || stored === 'system') return stored;
  } catch { /* storage blocked */ }
  return DEFAULT_THEME;
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(read);
  const [systemDark, setSystemDark] = useState(() => window.matchMedia('(prefers-color-scheme: dark)').matches);
  useEffect(() => {
    const query = window.matchMedia('(prefers-color-scheme: dark)');
    const onChange = (event: MediaQueryListEvent) => setSystemDark(event.matches);
    query.addEventListener('change', onChange);
    return () => query.removeEventListener('change', onChange);
  }, []);
  const resolvedTheme = useMemo<ResolvedTheme>(
    () => theme === 'system' ? (systemDark ? 'dark' : 'light') : theme,
    [theme, systemDark],
  );
  useEffect(() => {
    const root = document.documentElement;
    root.classList.remove('light', 'dark');
    root.classList.add(resolvedTheme);
    root.style.colorScheme = resolvedTheme;
    document.querySelector("meta[name='theme-color']")?.setAttribute('content', resolvedTheme === 'dark' ? '#0a0a0a' : '#fafafa');
  }, [resolvedTheme]);
  const value = useMemo<ThemeState>(() => ({
    theme,
    resolvedTheme,
    setTheme: next => {
      try { localStorage.setItem(STORAGE_KEY, next); } catch { /* storage blocked */ }
      setThemeState(next);
    },
  }), [theme, resolvedTheme]);
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
  const context = useContext(ThemeContext);
  if(!context) throw new Error('useTheme must be used within a ThemeProvider');
  return context;
}