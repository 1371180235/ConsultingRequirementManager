import { describe, expect, it } from 'vitest';
import { canManagePlanning } from './PlanningPages';

describe('planning write permissions', () => {
  it('keeps project, annual-plan, version and baseline changes with governance roles', () => {
    expect(canManagePlanning('admin')).toBe(true);
    expect(canManagePlanning('leader')).toBe(true);
    expect(canManagePlanning('manager')).toBe(false);
    expect(canManagePlanning('sales')).toBe(false);
    expect(canManagePlanning('developer')).toBe(false);
    expect(canManagePlanning('customer')).toBe(false);
  });
});
