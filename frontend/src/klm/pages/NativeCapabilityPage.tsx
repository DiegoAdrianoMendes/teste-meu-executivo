import { Navigate, useNavigate, useParams } from 'react-router-dom';
import { NativeCapabilityWorkspace } from '@/klm/components/NativeCapabilityWorkspace';
import { isKlmNativeDomain } from '@/klm/native-domains';

export default function NativeCapabilityPage() {
  const navigate = useNavigate();
  const { domain = '', capability = '' } = useParams<{ domain: string; capability: string }>();

  if (!isKlmNativeDomain(domain) || !capability) {
    return <Navigate to="/app/ferramentas?tab=coverage" replace />;
  }

  return (
    <NativeCapabilityWorkspace
      domain={domain}
      capability={capability}
      onBack={() => navigate(-1)}
      onOpenInventory={() => navigate(`/app/ferramentas/inventario?capability=${encodeURIComponent(`${domain}.${capability}`)}`)}
    />
  );
}
