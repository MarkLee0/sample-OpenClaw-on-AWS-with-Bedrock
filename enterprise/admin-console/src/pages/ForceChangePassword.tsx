import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import { useChangePassword } from '../hooks/useApi';
import { AlertCircle, Check, X, LogOut, KeyRound } from 'lucide-react';
import ClawForgeLogo from '../components/ClawForgeLogo';

const COMPLEXITY_RULES = [
  { label: 'At least 8 characters', test: (pw: string) => pw.length >= 8 },
  { label: 'One uppercase letter', test: (pw: string) => /[A-Z]/.test(pw) },
  { label: 'One lowercase letter', test: (pw: string) => /[a-z]/.test(pw) },
  { label: 'One digit', test: (pw: string) => /\d/.test(pw) },
  { label: 'One special character', test: (pw: string) => /[!@#$%^&*()\-_=+\[\]{};:'",.<>?/\\|`~]/.test(pw) },
];

export default function ForceChangePassword() {
  const { user, logout, updateToken } = useAuth();
  const navigate = useNavigate();
  const changePassword = useChangePassword();

  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [error, setError] = useState('');

  // Navigate away once mustChangePassword flips to false (after updateToken propagates)
  useEffect(() => {
    if (user && !user.mustChangePassword) {
      navigate(user.role === 'employee' ? '/portal' : '/dashboard', { replace: true });
    }
  }, [user?.mustChangePassword, navigate, user?.role]);

  const allRulesPassed = COMPLEXITY_RULES.every(r => r.test(newPassword));
  const passwordsMatch = newPassword === confirmPassword && confirmPassword.length > 0;
  const canSubmit = currentPassword && allRulesPassed && passwordsMatch && !changePassword.isPending;

  const handleSubmit = async () => {
    if (!canSubmit) return;
    setError('');
    try {
      const result = await changePassword.mutateAsync({ currentPassword, newPassword });
      updateToken(result.token);
      // Navigation happens via useEffect when mustChangePassword flips to false
    } catch (e: any) {
      const detail = e?.response?.detail || e?.message || 'Failed to change password';
      setError(typeof detail === 'string' ? detail : JSON.stringify(detail));
    }
  };

  return (
    <div className="min-h-screen bg-dark-bg flex items-center justify-center p-4">
      <div className="w-full max-w-md">
        {/* Logo */}
        <div className="text-center mb-8">
          <div className="inline-flex mb-4"><ClawForgeLogo size={56} animate="idle" /></div>
          <h1 className="text-2xl font-bold text-text-primary">Set Your Password</h1>
          <p className="text-sm text-text-muted mt-1">
            Welcome, <span className="text-primary-light">{user?.name}</span>. You must set a personal password before continuing.
          </p>
        </div>

        {/* Form */}
        <div className="rounded-xl border border-dark-border bg-dark-card p-6 mb-4">
          {error && (
            <div className="flex items-center gap-2 rounded-lg bg-red-500/10 border border-red-500/20 px-3 py-2 mb-4">
              <AlertCircle size={16} className="text-red-400 shrink-0" />
              <span className="text-sm text-red-400">{error}</span>
            </div>
          )}

          <div className="space-y-4">
            <div>
              <label className="block text-sm text-text-muted mb-1">Current Password</label>
              <input
                type="password"
                value={currentPassword}
                onChange={e => setCurrentPassword(e.target.value)}
                placeholder="Enter current password"
                className="w-full rounded-lg border border-dark-border bg-dark-bg px-4 py-2.5 text-sm text-text-primary placeholder:text-text-muted focus:border-primary focus:outline-none"
              />
            </div>

            <div>
              <label className="block text-sm text-text-muted mb-1">New Password</label>
              <input
                type="password"
                value={newPassword}
                onChange={e => setNewPassword(e.target.value)}
                placeholder="Enter new password"
                className="w-full rounded-lg border border-dark-border bg-dark-bg px-4 py-2.5 text-sm text-text-primary placeholder:text-text-muted focus:border-primary focus:outline-none"
              />
            </div>

            <div>
              <label className="block text-sm text-text-muted mb-1">Confirm New Password</label>
              <input
                type="password"
                value={confirmPassword}
                onChange={e => setConfirmPassword(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && canSubmit && handleSubmit()}
                placeholder="Confirm new password"
                className="w-full rounded-lg border border-dark-border bg-dark-bg px-4 py-2.5 text-sm text-text-primary placeholder:text-text-muted focus:border-primary focus:outline-none"
              />
              {confirmPassword && !passwordsMatch && (
                <p className="text-xs text-red-400 mt-1">Passwords do not match</p>
              )}
            </div>

            {/* Complexity indicators */}
            {newPassword.length > 0 && (
              <div className="rounded-lg bg-dark-bg border border-dark-border p-3 space-y-1.5">
                {COMPLEXITY_RULES.map(rule => {
                  const passed = rule.test(newPassword);
                  return (
                    <div key={rule.label} className="flex items-center gap-2">
                      {passed
                        ? <Check size={14} className="text-green-400" />
                        : <X size={14} className="text-text-muted" />}
                      <span className={`text-xs ${passed ? 'text-green-400' : 'text-text-muted'}`}>{rule.label}</span>
                    </div>
                  );
                })}
              </div>
            )}

            <button
              onClick={handleSubmit}
              disabled={!canSubmit}
              className="w-full flex items-center justify-center gap-2 rounded-lg bg-primary px-4 py-2.5 text-sm font-medium text-white hover:bg-primary/90 disabled:opacity-50 transition-colors"
            >
              <KeyRound size={16} />
              {changePassword.isPending ? 'Changing...' : 'Set Password & Continue'}
            </button>
          </div>
        </div>

        {/* Logout */}
        <div className="text-center">
          <button
            onClick={logout}
            className="inline-flex items-center gap-1.5 text-xs text-text-muted hover:text-text-primary transition-colors"
          >
            <LogOut size={12} /> Sign out
          </button>
        </div>
      </div>
    </div>
  );
}
