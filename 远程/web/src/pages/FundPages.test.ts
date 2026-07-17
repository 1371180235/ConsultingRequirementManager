import { describe, expect, it } from 'vitest';
import { applicationFromApi, canWriteFundEntries, childNodes, normalizeFundTree, unallocatedBudget } from './FundPages';

describe('fund tree hierarchy', () => {
  it('allows only project governance roles to write fund entries', () => {
    expect(['admin', 'leader', 'manager'].filter(canWriteFundEntries)).toEqual(['admin', 'leader', 'manager']);
    expect(canWriteFundEntries('sales')).toBe(false);
    expect(canWriteFundEntries('developer')).toBe(false);
    expect(canWriteFundEntries('customer')).toBe(false);
  });

  it('keeps repeated database ids in their own levels and subtracts direct children only', () => {
    const tree = normalizeFundTree({
      id: 1,
      type: 'project',
      name: '项目',
      budget: 1000,
      actual: 0,
      children: [{
        id: 1,
        type: 'annual_plan',
        name: '2027 年度',
        budget: 600,
        actual: 0,
        children: [
          {
            id: 1,
            type: 'version',
            name: '基础规划版',
            budget: 250,
            actual: 0,
            children: [{ id: 1, type: 'requirement', name: '资金优化', budget: 100, actual: 0 }],
          },
          { id: 2, type: 'version', name: '资金优化版', budget: 300, actual: 0, children: [] },
        ],
      }],
    });

    const project = tree.nodes.find((item) => item.level === 'project');
    const years = childNodes(tree.nodes, project);
    const versions = childNodes(tree.nodes, years[0]);
    const requirements = childNodes(tree.nodes, versions[0]);

    expect(years.map((item) => [item.level, item.name])).toEqual([['year', '2027 年度']]);
    expect(versions.map((item) => [item.level, item.name])).toEqual([
      ['version', '基础规划版'],
      ['version', '资金优化版'],
    ]);
    expect(requirements.map((item) => [item.level, item.name])).toEqual([['requirement', '资金优化']]);
    expect(unallocatedBudget(tree.nodes, project!)).toBe(400);
    expect(unallocatedBudget(tree.nodes, years[0])).toBe(50);
    expect(unallocatedBudget(tree.nodes, requirements[0])).toBeNull();
  });

  it('uses the stable applicant name returned by the server', () => {
    const item = applicationFromApi({
      id: 9,
      project_id: 1,
      annual_plan_id: 2,
      version_id: null,
      title: '年度资金申报',
      amount: '100.00',
      status: 'draft',
      applicant_id: 1,
      applicant_name: '申报编辑人',
    }, '测试项目', 2027);

    expect(item.owner).toBe('申报编辑人');
    expect(item.owner).not.toMatch(/^#/);
  });

  it('keeps annual and unscoped planning-pool branches visible in the four levels', () => {
    const tree = normalizeFundTree({
      id: 1,
      type: 'project',
      name: '项目',
      budget: 1000,
      actual: 0,
      children: [
        {
          id: 10,
          type: 'annual_plan',
          name: '2027 年度',
          budget: 600,
          actual: 0,
          children: [{
            id: 'planning-plan-10',
            type: 'version',
            name: '年度内待规划',
            budget: 0,
            actual: 0,
            planning_pool: true,
            children: [{ id: 31, type: 'requirement', name: '年度待规划需求', budget: 0, actual: 0, planning_pool: true }],
          }],
        },
        {
          id: 'planning-pool',
          type: 'annual_plan',
          name: '无年度待规划',
          budget: 0,
          actual: 0,
          planning_pool: true,
          children: [{ id: 'planning-unscoped', type: 'version', name: '待规划需求', budget: 0, actual: 0, planning_pool: true, children: [] }],
        },
      ],
    });

    const project = tree.nodes.find((item) => item.level === 'project');
    const years = childNodes(tree.nodes, project);
    const annualPool = childNodes(tree.nodes, years[0])[0];

    expect(years.map((item) => [item.id, item.name, item.planningPool])).toEqual([
      ['10', '2027 年度', false],
      ['planning-pool', '无年度待规划', true],
    ]);
    expect(annualPool).toMatchObject({ id: 'planning-plan-10', name: '年度内待规划', planningPool: true });
    expect(childNodes(tree.nodes, annualPool)[0]).toMatchObject({ id: '31', planningPool: true });
  });
});
