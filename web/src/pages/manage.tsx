// Manage (#316): the pages of an OP-XY running XY Forever (Device, Mods, Files). Without a connection it shows Connect
// in their place, at the same address, which looks for the OP-XY by itself; the page follows once connected.
import { Outlet } from '@tanstack/react-router';
import { useConnection } from '../connection.ts';
import ConnectPage from './connect.tsx';

export default function ManagePage() {
  const connected = useConnection((s) => s.client !== null);
  return connected ? <Outlet /> : <ConnectPage />;
}
