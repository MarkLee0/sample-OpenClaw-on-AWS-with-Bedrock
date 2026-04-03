import { useState, useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  RefreshCw, Users, CheckCircle, XCircle, AlertCircle,
  Trash2, MessageSquare, Clock, Activity, Wifi,
} from 'lucide-react';
import { Card, Badge, Button, PageHeader, StatCard, Tabs } from '../components/ui';
import { api } from '../api/client';
import { IM_ICONS } from '../components/IMIcons';

// ─── Types ────────────────────────────────────────────────────────────────────

interface IMChannel {
  id: string; label: string; enterprise: boolean;
  status: 'connected' | 'configured' | 'not_connected';
  connectedEmployees: number; gatewayInfo: string;
}

interface ChannelConnection {
  empId: string; empName: string; positionName: string; departmentName: string;
  channelUserId: string; connectedAt: string; sessionCount: number; lastActive: string;
}

// ─── helpers ─────────────────────────────────────────────────────────────────

function timeAgo(iso: string): string {
  if (!iso) return '—';
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

function shortDate(iso: string): string {
  if (!iso) return '—';
  try { return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: '2-digit' }); }
  catch { return '—'; }
}

const CHANNEL_LABELS: Record<string, string> = {
  telegram: 'Telegram', discord: 'Discord', feishu: 'Feishu / Lark',
  dingtalk: 'DingTalk', slack: 'Slack', teams: 'Microsoft Teams',
  googlechat: 'Google Chat', whatsapp: 'WhatsApp', wechat: 'WeChat',
};

const ENTERPRISE_CHANNELS = ['telegram', 'discord', 'feishu', 'dingtalk', 'slack', 'teams', 'googlechat', 'whatsapp', 'wechat'];

// ─── Connection Row ───────────────────────────────────────────────────────────

function ConnectionRow({ conn, channel, onRevoke, revoking }: {
  conn: ChannelConnection; channel: string;
  onRevoke: () => void; revoking: boolean;
}) {
  const [confirming, setConfirming] = useState(false);

  return (
    <tr className="border-b border-dark-border/30 hover:bg-dark-hover/20 transition-colors">
      <td className="py-3 px-4">
        <p className="text-sm font-medium text-text-primary">{conn.empName}</p>
        <p className="text-xs text-text-muted">{conn.positionName} · {conn.departmentName}</p>
      </td>
      <td className="py-3 px-4">
        <code className="text-xs font-mono text-text-secondary bg-dark-bg px-2 py-1 rounded">
          {conn.channelUserId.length > 20 ? conn.channelUserId.slice(0, 18) + '…' : conn.channelUserId}
        </code>
      </td>
      <td className="py-3 px-4 text-xs text-text-muted">{shortDate(conn.connectedAt)}</td>
      <td className="py-3 px-4">
        <div className="flex items-center gap-1.5">
          <MessageSquare size={12} className="text-text-muted" />
          <span className="text-sm font-medium text-text-primary">{conn.sessionCount || 0}</span>
        </div>
      </td>
      <td className="py-3 px-4 text-xs text-text-muted">{timeAgo(conn.lastActive)}</td>
      <td className="py-3 px-4">
        {confirming ? (
          <div className="flex items-center gap-1.5">
            <span className="text-xs text-danger">Disconnect?</span>
            <Button variant="danger" size="sm" disabled={revoking}
              onClick={() => { onRevoke(); setConfirming(false); }}>
              {revoking ? <RefreshCw size={11} className="animate-spin" /> : 'Yes'}
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setConfirming(false)}>No</Button>
          </div>
        ) : (
          <Button variant="ghost" size="sm"
            className="text-text-muted hover:text-danger hover:border-danger/30"
            onClick={() => setConfirming(true)}>
            <Trash2 size={13} /> Disconnect
          </Button>
        )}
      </td>
    </tr>
  );
}

// ─── Channel Tab Content ──────────────────────────────────────────────────────

function ChannelConnections({ channel, connections, channelStatus, onRevoke }: {
  channel: string; connections: ChannelConnection[];
  channelStatus?: IMChannel; onRevoke: (channelUserId: string) => void;
}) {
  const qc = useQueryClient();
  const [testResult, setTestResult] = useState<{ ok: boolean; botName?: string; error?: string } | null>(null);
  const [testing, setTesting] = useState(false);

  const revokeMutation = useMutation({
    mutationFn: ({ ch, uid }: { ch: string; uid: string }) =>
      api.del(`/bindings/user-mappings?channel=${ch}&channelUserId=${uid}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['im-channel-connections'] });
      qc.invalidateQueries({ queryKey: ['im-channels'] });
    },
  });

  const handleTestConnection = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const result = await api.post<{ ok: boolean; botName?: string; error?: string }>(
        `/admin/im-channels/${channel}/test`, {}
      );
      setTestResult(result);
    } catch {
      setTestResult({ ok: false, error: 'Request failed' });
    }
    setTesting(false);
  };

  const Icon = IM_ICONS[channel];
  const label = CHANNEL_LABELS[channel] || channel;
  const isConfigured = channelStatus?.status === 'connected' || channelStatus?.status === 'configured';

  return (
    <div className="space-y-4">
      {/* Channel header */}
      <div className="flex items-center gap-4 rounded-xl border px-4 py-3 bg-surface-dim border-dark-border/50">
        <div className="shrink-0">{Icon ? <Icon size={32} /> : <Wifi size={32} className="text-text-muted" />}</div>
        <div className="flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <h3 className="text-sm font-semibold text-text-primary">{label} Bot</h3>
            {channelStatus?.status === 'connected' && <Badge color="success" dot>Bot Active</Badge>}
            {channelStatus?.status === 'configured' && <Badge color="warning" dot>Bot Configured</Badge>}
            {(!channelStatus || channelStatus.status === 'not_connected') && <Badge color="default">Bot Not Connected</Badge>}
            {testResult && (
              <span className={`text-xs ${testResult.ok ? 'text-success' : 'text-danger'}`}>
                {testResult.ok ? `Connection OK — @${testResult.botName}` : `Failed: ${testResult.error}`}
              </span>
            )}
          </div>
          {channelStatus?.gatewayInfo && (
            <p className="text-[10px] text-text-muted font-mono mt-0.5">{channelStatus.gatewayInfo}</p>
          )}
        </div>
        <div className="flex items-center gap-3 shrink-0">
          {isConfigured && (
            <Button variant="default" size="sm" onClick={handleTestConnection} disabled={testing}>
              {testing ? <RefreshCw size={13} className="animate-spin" /> : <CheckCircle size={13} />}
              {testing ? 'Testing...' : 'Test Connection'}
            </Button>
          )}
          <div className="text-right">
            <p className="text-2xl font-bold text-text-primary">{connections.length}</p>
            <p className="text-xs text-text-muted">employees connected</p>
          </div>
        </div>
      </div>

      {/* Setup guide for unconfigured bots */}
      {(!channelStatus || channelStatus.status === 'not_connected') && (
        <div className="rounded-xl border border-warning/20 bg-warning/5 px-4 py-3 text-sm">
          <p className="font-medium text-text-primary mb-1">Bot not configured</p>
          <p className="text-xs text-text-muted mb-2">
            Configure the {label} bot via the <strong>OpenClaw Gateway UI</strong> (one-time setup by IT Admin):
          </p>
          <ol className="text-xs text-text-muted space-y-0.5 list-decimal list-inside">
            <li>SSM port-forward: <code className="bg-dark-hover px-1 rounded">aws ssm start-session --target $INSTANCE_ID --document-name AWS-StartPortForwardingSession --parameters portNumber=18789,localPortNumber=18789</code></li>
            <li>Open <code className="bg-dark-hover px-1 rounded">http://localhost:18789</code> → Channels → Add {label}</li>
            <li>Paste your bot token / credentials → Save</li>
            <li>Come back here and click <strong>Refresh</strong> to confirm status</li>
          </ol>
        </div>
      )}

      {/* Connections table */}
      {connections.length === 0 ? (
        <div className="rounded-xl bg-surface-dim border border-dark-border/30 py-12 text-center">
          <Users size={28} className="mx-auto mb-3 text-text-muted opacity-40" />
          <p className="text-sm text-text-muted">No employees connected via {label} yet</p>
          <p className="text-xs text-text-muted mt-1">Employees connect from Portal → Connect IM</p>
        </div>
      ) : (
        <Card className="p-0 overflow-hidden">
          <table className="w-full">
            <thead>
              <tr className="border-b border-dark-border/50 bg-surface-dim">
                <th className="py-2.5 px-4 text-left text-xs font-medium text-text-muted uppercase tracking-wider">Employee</th>
                <th className="py-2.5 px-4 text-left text-xs font-medium text-text-muted uppercase tracking-wider">Channel User ID</th>
                <th className="py-2.5 px-4 text-left text-xs font-medium text-text-muted uppercase tracking-wider">Connected</th>
                <th className="py-2.5 px-4 text-left text-xs font-medium text-text-muted uppercase tracking-wider">Sessions</th>
                <th className="py-2.5 px-4 text-left text-xs font-medium text-text-muted uppercase tracking-wider">Last Active</th>
                <th className="py-2.5 px-4 text-left text-xs font-medium text-text-muted uppercase tracking-wider">Action</th>
              </tr>
            </thead>
            <tbody>
              {connections.map(conn => (
                <ConnectionRow
                  key={conn.channelUserId}
                  conn={conn}
                  channel={channel}
                  revoking={revokeMutation.isPending}
                  onRevoke={() => revokeMutation.mutate({ ch: channel, uid: conn.channelUserId })}
                />
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}

// ─── Main Page ────────────────────────────────────────────────────────────────

export default function IMChannels() {
  const qc = useQueryClient();
  const [activeChannel, setActiveChannel] = useState('telegram');

  const { data: channels = [], isLoading: channelsLoading, refetch, isFetching } = useQuery<IMChannel[]>({
    queryKey: ['im-channels'],
    queryFn: () => api.get('/admin/im-channels'),
    refetchInterval: 60_000,
  });

  const { data: connectionsData, isLoading: connLoading, refetch: refetchConn } = useQuery<{
    connections: Record<string, ChannelConnection[]>;
  }>({
    queryKey: ['im-channel-connections'],
    queryFn: () => api.get('/admin/im-channel-connections'),
    refetchInterval: 60_000,
  });

  const connections = connectionsData?.connections || {};
  const channelStatusMap = Object.fromEntries(channels.map(c => [c.id, c]));

  // Total stats
  const totalConnected = Object.values(connections).reduce((s, arr) => s + arr.length, 0);
  const activeChannels = Object.keys(connections).filter(ch => connections[ch].length > 0);
  const totalSessions = Object.values(connections).flat().reduce((s, c) => s + (c.sessionCount || 0), 0);

  // Build tabs — only enterprise channels
  const tabs = ENTERPRISE_CHANNELS.map(ch => ({
    id: ch,
    label: CHANNEL_LABELS[ch] || ch,
    count: connections[ch]?.length || 0,
  }));

  const handleRefresh = useCallback(() => {
    refetch();
    refetchConn();
  }, [refetch, refetchConn]);

  return (
    <div>
      <PageHeader
        title="IM Channels"
        description="Monitor employee IM connections across all channels. Manage pairings and view session activity."
        actions={
          <Button variant="default" size="sm" onClick={handleRefresh} disabled={isFetching || connLoading}>
            <RefreshCw size={14} className={(isFetching || connLoading) ? 'animate-spin' : ''} /> Refresh
          </Button>
        }
      />

      {/* Stats */}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4 mb-6">
        <StatCard title="Paired Employees" value={totalConnected} subtitle="across all channels" icon={<Users size={22} />} color="primary" />
        <StatCard title="Active Channels" value={activeChannels.length} subtitle="with at least 1 employee" icon={<CheckCircle size={22} />} color="success" />
        <StatCard title="Total Sessions" value={totalSessions} subtitle="all-time invocations" icon={<Activity size={22} />} color="info" />
        <StatCard title="Bot Connections" value={channels.filter(c => c.status === 'connected').length} subtitle={`of ${channels.filter(c => c.enterprise).length} enterprise bots`} icon={<Wifi size={22} />} color="cyan" />
      </div>

      {/* Channel tabs */}
      <Tabs
        tabs={tabs}
        activeTab={activeChannel}
        onChange={setActiveChannel}
      />

      <div className="mt-6">
        {connLoading ? (
          <div className="flex justify-center py-16">
            <RefreshCw size={24} className="animate-spin text-text-muted" />
          </div>
        ) : (
          <ChannelConnections
            key={activeChannel}
            channel={activeChannel}
            connections={connections[activeChannel] || []}
            channelStatus={channelStatusMap[activeChannel]}
            onRevoke={() => {}}
          />
        )}
      </div>

      {/* Info footer */}
      <div className="mt-6 rounded-xl bg-info/5 border border-info/20 px-4 py-3 text-xs text-info">
        Employees connect via <strong>Portal → Connect IM</strong>. Disconnecting removes their SSM mapping — they can reconnect anytime by scanning again.
        Employee-initiated disconnects are also available from their Portal.
      </div>
    </div>
  );
}
