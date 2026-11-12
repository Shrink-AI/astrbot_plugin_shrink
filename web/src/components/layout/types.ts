import type { LucideIcon } from 'lucide-react';

export type NavItem = {
  key: string;
  title: string;
  icon: LucideIcon;
  badge?: number;
};

export type NavGroupProps = {
  title?: string;
  items: NavItem[];
  active: string;
  onSelect: (key: string) => void;
};