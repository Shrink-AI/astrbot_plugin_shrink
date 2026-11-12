# 后台前端

AstrBot 插件的 WebUI。Vite + React 19 + TypeScript，界面用 Tailwind CSS v4 和 shadcn/ui，外壳布局沿用 [shadcn-admin](https://github.com/satnaing/shadcn-admin) 的侧边栏 + 顶栏结构。构建产物输出到仓库根目录的 `static/`，由 FastAPI 直接托管。

## 目录

```
src/
  main.tsx                 ThemeProvider + App
  App.tsx                  会话状态、页面注册表、SidebarProvider 外壳
  components/
    layout/                AppSidebar、NavGroup、Header、Main、SkipToMain
    theme-provider.tsx     浅色/深色/跟随系统
    theme-switch.tsx       顶栏主题菜单
    profile-dropdown.tsx   管理员菜单（刷新 / 设置 / 退出）
    confirm-dialog.tsx     删除、撤销这类操作走 AlertDialog 确认（`useConfirm()`）
    password-input.tsx     带显示/隐藏切换的密码框
    ui/                    shadcn 生成的组件，只读
  pages/                   14 个页面，每个导出一个组件
  shared/
    components.tsx         Panel / PageHead / Badge / Empty / Pagination / Modal / Facts
    dashboard.tsx          页面共享的配置、状态、busy、action
    format.ts              文案与格式化
    RunDetail / LearningDetail / EditContent  跨页共用弹窗
  api/                     请求封装与类型
  index.css                Tailwind 入口、语义色变量、暗色主题
```

## 布局

`App.tsx` 里是 `SidebarProvider` → `AppSidebar`（分组导航，`SidebarContent` 带 `.nav` 类）+ `SidebarInset` → `Header`（侧栏开关、面包屑、刷新、主题、管理员菜单）+ `Main`（`max-w-[1240px]` 居中内容区）。页面只管自己的内容，不要再画侧边栏或顶栏。

新增页面：在 `App.tsx` 的 `sections` 里加一项（`key`/`name`/`icon`/`view`），再到 `pages/` 写组件。

## 主题

`src/index.css` 里是 shadcn 的语义色变量，取值照抄 [shadcn-admin](https://github.com/satnaing/shadcn-admin) 的 `theme.css`（slate 中性色、`--radius: 0.625rem`、浅色背景纯白、暗色卡片略亮），另加了自定义 `success`/`warning`/`info`。`ThemeProvider` 往 `<html>` 上挂 `light`/`dark`，选择存在 `localStorage` 的 `quartz-theme`。改配色只改 `:root` 与 `.dark` 两组变量。

## 约定

- 只用语义色：`bg-card`、`text-muted-foreground`、`border-border`、`text-success`……不要 `#hex`、`rgb()`、`bg-blue-500`。
- 间距用 `flex`/`grid` + `gap-*`；不要 `space-x-*`、`space-y-*`。等宽高用 `size-*`。
- 条件类名用 `cn()`（`import { cn } from 'cn'`）。
- 图标用 `lucide-react`；放进按钮时加 `data-icon="inline-start"` / `inline-end`，不要自己写 `size-4`。
- 导入用别名 `@/`（`@/components/ui/button`、`@/shared/components`、`@/api/client`）。
- 卡片、徽章、弹窗、空态、分页一律用 `shared/components.tsx` 的封装，不要在页面里重写。
- 主列表（记忆、整理记录）用 `TableToolbar` + `TableShell` + `Pagination`，照 shadcn-admin 的 data-table 摆：工具条在表格上方、表格套一层 `rounded-md border`、分页在表格下方，**不要**再把这些包进 `Panel` 卡片。`Panel` 只用于页面里的次级信息块。
- 筛选用 `components/data-table/faceted-filter.tsx`（虚线描边按钮 + Popover 勾选列表），不要用原生 `<select>` 当表格筛选；排序表头用 `column-header.tsx`；行内操作放 `⋯` 下拉菜单；选中后的批量操作放 `bulk-actions.tsx`（底部居中浮动胶囊，带 `.selection-bar` 类名）。
- 表格要横滚时给 `Table` 加 `min-w-[...]`，滚动条落在 `TableShell` 内部，页面本身不能横向溢出（手机回归会查）。
- 长文本表格单元格要 `whitespace-normal`（`TableCell` 默认 nowrap）；数字列用 `tabular-nums`。
- 下拉筛选优先 `NativeSelect`（原生 `<select>`，浏览器测试依赖 `selectOption`）。
- 加组件：`cd web && pnpm dlx shadcn@latest add <名称>`。npm 官方源慢时先 `$env:npm_config_registry='https://registry.npmmirror.com'`（Windows PowerShell）。
- 破坏性操作不要用原生 `confirm()`，用 `useConfirm()`（`@/components/confirm-dialog`），它渲染的是 AlertDialog。

## 构建与检查

```bash
cd web
npm ci
npm run build            # tsc --noEmit && vite build，产物写到 ../static
node_modules/.bin/tsc --noEmit
```

后端要允许内联样式（`style-src 'unsafe-inline'`）：Sidebar、Radix 弹层都靠 style 属性定位，CSP 在 `shrink/api/auth.py`。

## 浏览器回归

```bash
python tools/preview.py --seed --port 6187 --data runtime/preview
node tools/check_dashboard.cjs runtime/preview 6187
```

脚本用 Playwright（`playwright-core` + 本机 Edge）真实点击桌面 1440 和手机 390 两个宽度。它会写入数据（改称呼、纠记忆、采纳提案），所以每次都要对**刚 `--seed` 的空库**跑，跑第二遍会对不上「一条待确认」这类计数断言；重跑前删掉数据目录再重启 preview。它依赖这些钩子，改界面时不要删：

- `.nav` 容器里的导航按钮，可访问名以页面名开头（概览/人物/记忆/定向学习/整理记录/人格/表情/设置）。手机上面板收进抽屉，脚本会先点侧栏开关。
- `.list-item`（人物、会话列表项）、`.chip`（称呼）、`.proposal`（待确认卡片，一条一个）、`.chatline.cited`（被引用的聊天行）、`.meme` / `.meme.off` / `.selection-bar`（表情页）。
- 表单可访问标签：`管理员密码`、`状态`（原生 select）、`记忆内容`、`说话习惯`、`主动插话概率`。
- 顶部提示条 `role="status"`；弹窗关闭按钮 `aria-label="关闭"`；管理员菜单按钮 `管理员菜单`，退出项在菜单里。
- 页面标题是 `<h1>`（`PageHead`），面板标题是 `<h2>`（`Panel`）。