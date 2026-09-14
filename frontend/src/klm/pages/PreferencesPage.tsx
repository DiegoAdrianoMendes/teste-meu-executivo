import { PersonalPreferencesPanel } from '@/components/admin/PersonalPreferencesPanel';
import { MyAccessSummary } from '@/klm/components/MyAccessSummary';

export default function PreferencesPage() {
  return (
    <div className="mx-auto w-full max-w-[1080px] space-y-5">
      <header>
        <p className="text-2xs font-semibold uppercase text-primary">Conta</p>
        <h1 className="mt-1 text-2xl font-semibold">Minhas preferências</h1>
        <p className="mt-1 text-xs leading-5 text-muted-foreground">
          Configuração individual para este workspace.
        </p>
      </header>
      <PersonalPreferencesPanel />
      <MyAccessSummary />
    </div>
  );
}
