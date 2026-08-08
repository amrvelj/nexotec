export interface DetailTab {
  id: string;
  label: string;
  /** Shown as a count badge when the tab holds a collection. Omit for
   * tabs that aren't a collection (e.g. Overview). */
  count?: number;
}
