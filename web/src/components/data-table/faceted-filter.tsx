import { CheckIcon, PlusCircleIcon } from 'lucide-react';
import { cn } from 'cn';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList, CommandSeparator,
} from '@/components/ui/command';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Separator } from '@/components/ui/separator';

export type FacetedFilterOption = { label: string; value: string };

export function FacetedFilter({ title, value, options, onChange }: {
  title: string;
  value: string;
  options: FacetedFilterOption[];
  onChange: (value: string) => void;
}) {
  const selected = options.find(option => option.value === value);
  return <Popover>
    <PopoverTrigger asChild>
      <Button variant="outline" size="sm" className="h-8 border-dashed">
        <PlusCircleIcon data-icon="inline-start" />
        {title}
        {selected && <>
          <Separator orientation="vertical" className="mx-2 h-4" />
          <Badge variant="secondary" className="rounded-sm px-1 font-normal">{selected.label}</Badge>
        </>}
      </Button>
    </PopoverTrigger>
    <PopoverContent className="w-52 p-0" align="start">
      <Command>
        <CommandInput placeholder={title} />
        <CommandList>
          <CommandEmpty>没有匹配项</CommandEmpty>
          <CommandGroup>
            {options.map(option => {
              const isSelected = option.value === value;
              return <CommandItem key={option.value} value={option.label}
                onSelect={() => onChange(isSelected ? '' : option.value)}>
                <div className={cn('flex size-4 items-center justify-center rounded-sm border border-primary',
                  isSelected ? 'bg-primary text-primary-foreground' : 'opacity-50 [&_svg]:invisible')}>
                  <CheckIcon className="size-3.5 text-background" />
                </div>
                <span>{option.label}</span>
              </CommandItem>;
            })}
          </CommandGroup>
          {selected && <>
            <CommandSeparator />
            <CommandGroup>
              <CommandItem value="清除筛选" onSelect={() => onChange('')} className="justify-center text-center">
                清除筛选
              </CommandItem>
            </CommandGroup>
          </>}
        </CommandList>
      </Command>
    </PopoverContent>
  </Popover>;
}