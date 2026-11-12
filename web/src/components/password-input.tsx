import { useState } from 'react';
import type { ComponentProps } from 'react';
import { Eye, EyeOff } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { cn } from 'cn';

export function PasswordInput({ className, disabled, ...props }: Omit<ComponentProps<typeof Input>, 'type'>) {
  const [visible, setVisible] = useState(false);
  return <div className={cn('relative', className)}>
    <Input type={visible ? 'text' : 'password'} className="pe-9" disabled={disabled} {...props} />
    <Button type="button" variant="ghost" size="icon-sm" disabled={disabled}
      aria-label={visible ? '隐藏密码' : '显示密码'}
      className="absolute end-1 top-1/2 -translate-y-1/2 text-muted-foreground"
      onClick={() => setVisible(value => !value)}>
      {visible ? <EyeOff /> : <Eye />}
    </Button>
  </div>;
}