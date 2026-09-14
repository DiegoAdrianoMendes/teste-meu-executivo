import { StaffContinuityPanel } from '@/components/admin/StaffContinuityPanel';
import { canPlatform } from '@/lib/platform-access';
import { useAppStore } from '@/store/app-context';

export default function BlackboxStaffContinuity() {
  const { state } = useAppStore();
  const canManage = canPlatform(state.currentUser, 'staff_reassignment', 'manage');

  return (
    <div className="mx-auto w-full max-w-[1500px] p-6">
      <StaffContinuityPanel
        surface="platform"
        users={[]}
        usePlatformLookup
        canManage={canManage}
        title="Continuidade da equipe do SaaS"
        description="Redistribua tickets e oportunidades quando um funcionário estiver ausente ou deixar a equipe. O Blackbox preserva autoria, registra a origem e nunca concede acesso ao workspace do cliente."
      />
    </div>
  );
}
