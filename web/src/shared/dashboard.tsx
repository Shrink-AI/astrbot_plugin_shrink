import { createContext, useContext } from 'react';
import type { Configuration, Status } from '../api/types';

export interface DashboardState extends Configuration {
  status: Status;
  busy: boolean;
  revision: number;
  refresh: () => void;
  action: (work: () => Promise<unknown>, success?: string) => Promise<void>;
  setError: (message: string) => void;
  signOut: () => void;
  go: (page: string) => void;
}
export const DashboardContext = createContext<DashboardState | null>(null);
export function useDashboard() {
  const value = useContext(DashboardContext);
  if(!value) throw new Error('Dashboard context is missing');
  return value;
}
