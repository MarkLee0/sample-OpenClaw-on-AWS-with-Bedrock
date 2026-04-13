import { createContext, useContext, useState, useEffect, ReactNode } from 'react';
import { api } from '../api/client';

export interface AuthUser {
  id: string;
  name: string;
  role: 'admin' | 'manager' | 'employee';
  departmentId: string;
  departmentName: string;
  positionId: string;
  positionName: string;
  agentId?: string;
  channels?: string[];
  mustChangePassword?: boolean;
}

interface AuthContextType {
  user: AuthUser | null;
  token: string | null;
  loading: boolean;
  login: (employeeId: string, password?: string) => Promise<void>;
  logout: () => void;
  updateToken: (newToken: string) => void;
  isAdmin: boolean;
  isManager: boolean;
  isEmployee: boolean;
}

const AuthContext = createContext<AuthContextType>({
  user: null, token: null, loading: true,
  login: async () => {}, logout: () => {}, updateToken: () => {},
  isAdmin: false, isManager: false, isEmployee: false,
});

export function useAuth() { return useContext(AuthContext); }

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Restore session from localStorage
  useEffect(() => {
    const saved = localStorage.getItem('openclaw_token');
    if (saved) {
      setToken(saved);
      // Verify token by calling /auth/me
      fetch('/api/v1/auth/me', { headers: { Authorization: `Bearer ${saved}` } })
        .then(r => r.ok ? r.json() : Promise.reject())
        .then(data => setUser({ ...data as AuthUser, mustChangePassword: (data as any).mustChangePassword ?? false }))
        .catch(() => { localStorage.removeItem('openclaw_token'); })
        .finally(() => setLoading(false));
    } else {
      setLoading(false);
    }
  }, []);

  // Set default auth header when token changes
  useEffect(() => {
    if (token) {
      (window as any).__openclaw_token = token;
    } else {
      delete (window as any).__openclaw_token;
    }
  }, [token]);

  const login = async (employeeId: string, password: string = '') => {
    const resp = await fetch('/api/v1/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ employeeId, password }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: 'Login failed' }));
      throw new Error(err.detail || 'Login failed');
    }
    const data = await resp.json();
    setToken(data.token);
    setUser({ ...data.employee as AuthUser, mustChangePassword: data.mustChangePassword ?? false });
    localStorage.setItem('openclaw_token', data.token);
  };

  const updateToken = (newToken: string) => {
    setToken(newToken);
    localStorage.setItem('openclaw_token', newToken);
    try {
      const payload = JSON.parse(atob(newToken.split('.')[1]));
      setUser(prev => prev ? { ...prev, mustChangePassword: payload.mustChangePassword ?? false } : prev);
    } catch { /* ignore decode errors */ }
  };

  const logout = () => {
    setUser(null);
    setToken(null);
    localStorage.removeItem('openclaw_token');
  };

  return (
    <AuthContext.Provider value={{
      user, token, loading, login, logout, updateToken,
      isAdmin: user?.role === 'admin',
      isManager: user?.role === 'manager',
      isEmployee: user?.role === 'employee',
    }}>
      {children}
    </AuthContext.Provider>
  );
}
