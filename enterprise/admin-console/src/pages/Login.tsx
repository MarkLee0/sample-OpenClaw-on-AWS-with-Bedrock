import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import { Bot, LogIn, AlertCircle } from 'lucide-react';
import ClawForgeLogo from '../components/ClawForgeLogo';

const DEMO_ACCOUNTS = [
  { id: 'emp-jiade',  name: 'JiaDe Wang',    role: 'Admin',    dept: 'Engineering', desc: 'Admin · Solutions Architect · full platform access' },
  { id: 'emp-chris',  name: 'Chris Morgan',  role: 'Admin',    dept: 'Platform',    desc: 'Admin · DevOps Engineer' },
  { id: 'emp-peter',  name: 'Peter Wu',      role: 'Employee', dept: 'Engineering', desc: 'Executive Agent · all tools' },
  { id: 'emp-alex',   name: 'Alex Rivera',   role: 'Manager',  dept: 'Product',     desc: 'Product Manager · Jira · research' },
  { id: 'emp-mike',   name: 'Mike Johnson',  role: 'Manager',  dept: 'Sales',       desc: 'Account Executive · CRM · WhatsApp' },
  { id: 'emp-jenny',  name: 'Jenny Liu',     role: 'Manager',  dept: 'HR',          desc: 'HR Specialist · email · calendar' },
  { id: 'emp-ryan',   name: 'Ryan Park',     role: 'Employee', dept: 'Engineering', desc: 'Software Engineer · shell · code · GitHub' },
  { id: 'emp-carol',  name: 'Carol Zhang',   role: 'Employee', dept: 'Finance',     desc: 'Finance Analyst · Excel · SAP' },
  { id: 'emp-emma',   name: 'Emma Chen',     role: 'Employee', dept: 'Customer',    desc: 'Customer Success · CRM · Slack' },
  { id: 'emp-rachel', name: 'Rachel Li',     role: 'Employee', dept: 'Legal',       desc: 'Legal Counsel · research · file' },
];

export default function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [empId, setEmpId] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleLogin = async (id: string, pwd?: string) => {
    setLoading(true);
    setError('');
    try {
      await login(id, pwd || password);
      const saved = localStorage.getItem('openclaw_token');
      if (saved) {
        const payload = JSON.parse(atob(saved.split('.')[1]));
        if (payload.mustChangePassword) navigate('/change-password');
        else if (payload.role === 'employee') navigate('/portal');
        else navigate('/dashboard');
      }
    } catch (e: any) {
      setError(e.message || 'Login failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-dark-bg flex items-center justify-center p-4">
      <div className="w-full max-w-md">
        {/* Logo */}
        <div className="text-center mb-8">
          <div className="inline-flex mb-4"><ClawForgeLogo size={56} animate="idle" /></div>
          <h1 className="text-2xl font-bold text-text-primary">OpenClaw Enterprise</h1>
          <p className="text-sm text-text-muted mt-1">on AgentCore · aws-samples</p>
        </div>

        {/* Login Form */}
        <div className="rounded-xl border border-dark-border bg-dark-card p-6 mb-6">
          <h2 className="text-lg font-semibold text-text-primary mb-4">Sign In</h2>
          {error && (
            <div className="flex items-center gap-2 rounded-lg bg-red-500/10 border border-red-500/20 px-3 py-2 mb-4">
              <AlertCircle size={16} className="text-red-400" />
              <span className="text-sm text-red-400">{error}</span>
            </div>
          )}
          <div className="space-y-4">
            <div>
              <label className="block text-sm text-text-muted mb-1">Employee ID</label>
              <input
                type="text" value={empId} onChange={e => setEmpId(e.target.value)}
                placeholder="emp-jiade or EMP-030"
                className="w-full rounded-lg border border-dark-border bg-dark-bg px-4 py-2.5 text-sm text-text-primary placeholder:text-text-muted focus:border-primary focus:outline-none"
              />
            </div>
            <div>
              <label className="block text-sm text-text-muted mb-1">Password</label>
              <input
                type="password" value={password} onChange={e => setPassword(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && empId && password && handleLogin(empId)}
                placeholder="Enter password"
                className="w-full rounded-lg border border-dark-border bg-dark-bg px-4 py-2.5 text-sm text-text-primary placeholder:text-text-muted focus:border-primary focus:outline-none"
              />
            </div>
            <button
              onClick={() => empId && password && handleLogin(empId)}
              disabled={!empId || !password || loading}
              className="w-full flex items-center justify-center gap-2 rounded-lg bg-primary px-4 py-2.5 text-sm font-medium text-white hover:bg-primary/90 disabled:opacity-50 transition-colors"
            >
              <LogIn size={16} /> {loading ? 'Signing in...' : 'Sign In'}
            </button>
          </div>
        </div>

        {/* Demo Accounts — reference only */}
        <div className="rounded-xl border border-dark-border bg-dark-card p-6">
          <h3 className="text-sm font-semibold text-text-muted uppercase tracking-wider mb-3">Demo Accounts</h3>
          <div className="space-y-2">
            {DEMO_ACCOUNTS.map(acc => {
              const isExec = acc.role === 'Executive';
              return (
                <div
                  key={acc.id}
                  onClick={() => setEmpId(acc.id)}
                  className={`flex items-center gap-3 rounded-lg px-3 py-2.5 transition-colors cursor-pointer ${
                    isExec
                      ? 'bg-warning/5 border border-warning/20 hover:bg-warning/10'
                      : 'hover:bg-dark-hover'
                  }`}
                >
                  <div className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-sm font-medium text-white ${
                    isExec ? 'bg-gradient-to-br from-warning to-orange-500'
                    : acc.role === 'Admin' ? 'bg-red-500'
                    : acc.role === 'Manager' ? 'bg-amber-500'
                    : 'bg-blue-500'
                  }`}>
                    {acc.name[0]}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className={`text-sm font-medium ${isExec ? 'text-warning' : 'text-text-primary'}`}>{acc.name}</span>
                      <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${
                        isExec ? 'bg-warning/15 text-warning'
                        : acc.role === 'Admin' ? 'bg-red-500/10 text-red-400'
                        : acc.role === 'Manager' ? 'bg-amber-500/10 text-amber-400'
                        : 'bg-blue-500/10 text-blue-400'
                      }`}>{acc.role}</span>
                      {isExec && <span className="text-[10px] px-1.5 py-0.5 rounded bg-purple-500/10 text-purple-400 font-medium">Claude Sonnet 4.6</span>}
                    </div>
                    <p className="text-xs text-text-muted truncate">{acc.id} · {acc.desc}</p>
                  </div>
                </div>
              );
            })}
          </div>
          <p className="text-[10px] text-text-muted mt-3 text-center">Click to fill Employee ID · Password required for all accounts</p>
        </div>

        {/* Contributor */}
        <div className="text-center mt-6">
          <p className="text-xs text-text-muted">
            Built by <a href="mailto:wjiad@amazon.com" className="text-primary-light hover:underline">wjiad@aws</a> · Contributions welcome
          </p>
        </div>
      </div>
    </div>
  );
}
