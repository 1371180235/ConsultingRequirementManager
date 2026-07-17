import { AlertCircle, CheckCircle2, LoaderCircle, X } from 'lucide-react';
import { useEffect, useId, type ButtonHTMLAttributes, type ReactNode } from 'react';
import { useApp } from '../state/AppContext';

export function Button({ className = '', variant = 'primary', children, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'secondary' | 'ghost' | 'danger' }) {
  return <button className={`button button--${variant} ${className}`} {...props}>{children}</button>;
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  return (
    <header className="page-header">
      <div className="page-header__copy">
        <h1>{title}</h1>
        {subtitle && <p>{subtitle}</p>}
      </div>
      {actions && <div className="page-header__actions">{actions}</div>}
    </header>
  );
}

export function StatusBadge({ children, tone = 'neutral' }: { children: ReactNode; tone?: 'success' | 'warning' | 'danger' | 'info' | 'neutral' }) {
  return <span className={`status status--${tone}`}>{children}</span>;
}

export function PriorityBadge({ value }: { value: string }) {
  const tone = value === 'P0' ? 'danger' : value === 'P1' ? 'warning' : value === 'P2' ? 'info' : 'neutral';
  return <StatusBadge tone={tone}>{value}</StatusBadge>;
}

export function Section({ title, action, children, className = '' }: { title?: string; action?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`section ${className}`}>
      {(title || action) && <div className="section__header">{title && <h2>{title}</h2>}{action}</div>}
      {children}
    </section>
  );
}

export function Metric({ label, value, detail, tone = 'default', icon }: { label: string; value: ReactNode; detail?: string; tone?: 'default' | 'success' | 'warning' | 'danger'; icon?: ReactNode }) {
  return (
    <article className={`metric metric--${tone}`}>
      <div className="metric__top"><span>{label}</span>{icon && <span className="metric__icon">{icon}</span>}</div>
      <strong>{value}</strong>
      {detail && <p>{detail}</p>}
    </article>
  );
}

export function DataState({ loading, error, children, onRetry }: { loading: boolean; error?: string; children: ReactNode; onRetry?: () => void }) {
  if (loading) return <div className="data-state" role="status"><LoaderCircle className="spin" size={22} /><span>数据加载中</span></div>;
  if (error) return (
    <div className="data-state data-state--error" role="alert">
      <AlertCircle size={22} />
      <div><strong>数据暂时不可用</strong><p>{error}</p></div>
      {onRetry && <Button variant="secondary" onClick={onRetry}>重试</Button>}
    </div>
  );
  return <>{children}</>;
}

export function Modal({ open, title, children, onClose, footer, wide = false }: { open: boolean; title: string; children: ReactNode; onClose: () => void; footer?: ReactNode; wide?: boolean }) {
  const titleId = useId();
  useEffect(() => {
    if (!open) return;
    const handle = (event: KeyboardEvent) => event.key === 'Escape' && onClose();
    window.addEventListener('keydown', handle);
    return () => window.removeEventListener('keydown', handle);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="modal-layer" role="presentation" onMouseDown={(event) => event.currentTarget === event.target && onClose()}>
      <div className={`modal ${wide ? 'modal--wide' : ''}`} role="dialog" aria-modal="true" aria-labelledby={titleId}>
        <header className="modal__header"><h2 id={titleId}>{title}</h2><button className="icon-button" onClick={onClose} aria-label="关闭"><X size={20} /></button></header>
        <div className="modal__body">{children}</div>
        {footer && <footer className="modal__footer">{footer}</footer>}
      </div>
    </div>
  );
}

export function EmptyState({ icon, title, detail, action }: { icon?: ReactNode; title: string; detail?: string; action?: ReactNode }) {
  return <div className="empty-state">{icon}<strong>{title}</strong>{detail && <p>{detail}</p>}{action}</div>;
}

export function ToastRegion() {
  const { toasts } = useApp();
  return (
    <div className="toast-region" aria-live="polite" aria-atomic="true">
      {toasts.map((toast) => <div className={`toast toast--${toast.tone ?? 'success'}`} key={toast.id}>
        {toast.tone === 'danger' ? <AlertCircle size={19} /> : <CheckCircle2 size={19} />}
        <div><strong>{toast.title}</strong>{toast.detail && <p>{toast.detail}</p>}</div>
      </div>)}
    </div>
  );
}

export function formatMoney(value: number): string {
  return `${new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 1 }).format(value)} 万元`;
}

export function statusTone(value: string): 'success' | 'warning' | 'danger' | 'info' | 'neutral' {
  if (/(已发布|已上线|已完成|通过|正常)/.test(value)) return 'success';
  if (/(驳回|超支|失败|禁用|紧急)/.test(value)) return 'danger';
  if (/(待|规划|审批|研发|进行)/.test(value)) return 'warning';
  if (/(已排期|已归档|冻结)/.test(value)) return 'info';
  return 'neutral';
}
