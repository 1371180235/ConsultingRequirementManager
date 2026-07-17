import { LoaderCircle } from 'lucide-react';
import { Navigate, Route, Routes } from 'react-router-dom';
import AppShell from './components/AppShell';
import { LoginPage, ChangePasswordPage } from './pages/AuthPages';
import DashboardPage from './pages/DashboardPage';
import { AnnualPlansPage, ProjectsPage, VersionsPage } from './pages/PlanningPages';
import { RequirementsPage, VersionComparePage } from './pages/RequirementsPages';
import { FundApplicationsPage, FundTracePage } from './pages/FundPages';
import { DeliverablesPage, OperationsPage } from './pages/DeliveryPages';
import { AuditPage, GlobalSearchPage, UsersPage } from './pages/SystemPages';
import { MilestonesPage, ReportsPage } from './pages/BusinessSupportPages';
import { useApp } from './state/AppContext';

export default function App() {
  const { user, authReady, mode, options } = useApp();
  if (!authReady) return <div className="app-loading" role="status"><span><LoaderCircle className="spin" size={27}/></span><strong>正在连接项目管理中心</strong></div>;
  if (!user) return <Routes><Route path="*" element={<LoginPage/>}/></Routes>;
  if (user.firstLogin) return <ChangePasswordPage/>;
  const canAudit = user.role === 'admin' || user.role === 'leader';
  if (mode === 'live' && options.projects.length === 0) return <Routes>
    <Route element={<AppShell/>}>
      <Route path="/projects" element={<ProjectsPage/>}/>
      <Route path="/users" element={user.role === 'admin' ? <UsersPage/> : <Navigate to="/projects" replace/>}/>
      <Route path="/audit" element={canAudit ? <AuditPage/> : <Navigate to="/projects" replace/>}/>
      <Route path="*" element={<Navigate to="/projects" replace/>}/>
    </Route>
  </Routes>;
  const canViewMoney = ['admin', 'leader', 'sales', 'manager'].includes(user.role);
  return <Routes>
    <Route element={<AppShell/>}>
      <Route index element={<Navigate to="/dashboard" replace/>}/>
      <Route path="/dashboard" element={<DashboardPage/>}/>
      <Route path="/projects" element={<ProjectsPage/>}/>
      <Route path="/annual-plans" element={<AnnualPlansPage/>}/>
      <Route path="/versions" element={<VersionsPage/>}/>
      <Route path="/requirements" element={<RequirementsPage/>}/>
      <Route path="/compare" element={<VersionComparePage/>}/>
      <Route path="/fund-trace" element={canViewMoney ? <FundTracePage/> : <Navigate to="/dashboard" replace/>}/>
      <Route path="/fund-applications" element={canViewMoney ? <FundApplicationsPage/> : <Navigate to="/dashboard" replace/>}/>
      <Route path="/deliverables" element={<DeliverablesPage/>}/>
      <Route path="/milestones" element={<MilestonesPage/>}/>
      <Route path="/operations" element={<OperationsPage/>}/>
      <Route path="/reports" element={<ReportsPage/>}/>
      <Route path="/search" element={<GlobalSearchPage/>}/>
      <Route path="/users" element={user.role === 'admin' ? <UsersPage/> : <Navigate to="/dashboard" replace/>}/>
      <Route path="/audit" element={canAudit ? <AuditPage/> : <Navigate to="/dashboard" replace/>}/>
      <Route path="*" element={<Navigate to="/dashboard" replace/>}/>
    </Route>
  </Routes>;
}
