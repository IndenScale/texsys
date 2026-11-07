declare module "*.worker.js?worker" {
  const WorkerFactory: new () => Worker;
  export default WorkerFactory;
}
