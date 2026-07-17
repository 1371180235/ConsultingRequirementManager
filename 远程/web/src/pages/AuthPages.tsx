import { AlertCircle, BarChart3, Eye, EyeOff, KeyRound, LoaderCircle, LockKeyhole, Server, ShieldCheck, UserRound } from 'lucide-react';
import { useState, type FormEvent } from 'react';
import { ApiError } from '../api';
import { useApp } from '../state/AppContext';
import { Button } from '../components/UI';

function PasswordField({ id, label, value, onChange, autoComplete }: { id: string; label: string; value: string; onChange: (value: string) => void; autoComplete: string }) {
  const [visible, setVisible] = useState(false);
  return <label className="field" htmlFor={id}><span>{label}</span><div className="input-with-icon"><LockKeyhole size={18} /><input id={id} type={visible ? 'text' : 'password'} value={value} onChange={(event) => onChange(event.target.value)} autoComplete={autoComplete} required /><button type="button" className="field-icon-button" onClick={() => setVisible((current) => !current)} aria-label={visible ? '隐藏密码' : '显示密码'}>{visible ? <EyeOff size={18} /> : <Eye size={18} />}</button></div></label>;
}

export function LoginPage() {
  const { login, authError, sessionReason, clearSessionReason } = useApp();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError('');
    clearSessionReason();
    setBusy(true);
    try { await login(username.trim(), password); }
    catch (reason) { setError(reason instanceof ApiError ? reason.message : '登录失败，请稍后重试。'); }
    finally { setBusy(false); }
  }

  return (
    <main className="auth-page">
      <section className="auth-brand" aria-label="系统信息">
        <div className="auth-brand__logo"><BarChart3 size={28} /><span>项目管理中心</span></div>
        <div className="auth-brand__content">
          <span className="auth-kicker"><ShieldCheck size={17} />企业级安全协作</span>
          <h1>从宏观规划到单项需求，让项目资金与交付始终对齐。</h1>
          <div className="auth-points">
            <span><Server size={19} />服务端统一存储</span>
            <span><ShieldCheck size={19} />角色与项目级权限</span>
            <span><KeyRound size={19} />单账号单有效会话</span>
          </div>
        </div>
        <p>咨询项目全流程需求管理系统</p>
      </section>
      <section className="auth-form-panel">
        <form className="auth-form" onSubmit={submit}>
          <div className="auth-form__heading"><span>安全登录</span><h2>欢迎回来</h2><p>使用管理员分配的账号进入工作台。</p></div>
          {(sessionReason || error) && <div className="form-alert" role="alert"><AlertCircle size={19} /><div><strong>{sessionReason ? '会话已结束' : '无法登录'}</strong><p>{sessionReason || error}</p></div></div>}
          {!error && !sessionReason && authError && <div className="form-alert form-alert--warning" role="status"><Server size={19} /><div><strong>服务暂不可用</strong><p>{authError}</p></div></div>}
          <label className="field" htmlFor="username"><span>账号</span><div className="input-with-icon"><UserRound size={18} /><input id="username" value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" placeholder="请输入账号" required autoFocus /></div></label>
          <PasswordField id="password" label="密码" value={password} onChange={setPassword} autoComplete="current-password" />
          <div className="login-meta"><span>本系统不开放自助注册</span><span>忘记密码请联系管理员重置</span></div>
          <Button className="button--full" disabled={busy}>{busy ? <><LoaderCircle className="spin" size={18} />正在验证</> : '登录工作台'}</Button>
        </form>
      </section>
    </main>
  );
}

export function ChangePasswordPage() {
  const { changePassword, logout } = useApp();
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const valid = next.length >= 10 && /[a-z]/.test(next) && /[A-Z]/.test(next) && /\d/.test(next) && /[^A-Za-z\d]/.test(next) && next === confirm;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!valid) { setError('请检查新密码强度和两次输入。'); return; }
    setBusy(true); setError('');
    try { await changePassword(current, next); }
    catch (reason) { setError(reason instanceof Error ? reason.message : '密码修改失败'); }
    finally { setBusy(false); }
  }
  return <main className="password-page"><section className="password-panel">
    <div className="password-panel__icon"><KeyRound size={26} /></div><span>首次登录</span><h1>设置你的新密码</h1><p>完成后才能进入工作台。</p>
    {error && <div className="form-alert" role="alert"><AlertCircle size={19} /><p>{error}</p></div>}
    <form onSubmit={submit}>
      <PasswordField id="current-password" label="当前密码" value={current} onChange={setCurrent} autoComplete="current-password" />
      <PasswordField id="new-password" label="新密码" value={next} onChange={setNext} autoComplete="new-password" />
      <PasswordField id="confirm-password" label="确认新密码" value={confirm} onChange={setConfirm} autoComplete="new-password" />
      <div className="password-rules" aria-label="密码要求"><span className={next.length >= 10 ? 'is-valid' : ''}>至少 10 位</span><span className={/[a-z]/.test(next) && /[A-Z]/.test(next) ? 'is-valid' : ''}>包含大小写字母</span><span className={/\d/.test(next) ? 'is-valid' : ''}>包含数字</span><span className={/[^A-Za-z\d]/.test(next) ? 'is-valid' : ''}>包含特殊字符</span></div>
      <Button className="button--full" disabled={busy || !valid}>{busy ? '正在保存' : '保存并进入工作台'}</Button>
      <button className="text-button" type="button" onClick={() => void logout()}>退出登录</button>
    </form>
  </section></main>;
}
