import {
  ActionIcon,
  AppShell,
  Badge,
  Burger,
  Group,
  NavLink,
  Stack,
  Text,
  Title,
  useMantineColorScheme,
} from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { NavLink as RouterLink, Outlet, useLocation } from 'react-router';
import { Suspense, useEffect } from 'react';
import { Loading } from '../shared/ui/Feedback';
export function Layout() {
  const [opened, { toggle, close }] = useDisclosure();
  const { setColorScheme, colorScheme } = useMantineColorScheme();
  const location = useLocation();
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [location.pathname]);
  return (
    <AppShell
      header={{ height: 72 }}
      navbar={{ width: 238, breakpoint: 'sm', collapsed: { mobile: !opened } }}
      padding={{ base: 'md', md: 'xl' }}
    >
      <a className="skip-link" href="#main-content">
        Pular para o conteúdo
      </a>
      <AppShell.Header>
        <Group h="100%" px="lg" justify="space-between">
          <Group gap="sm">
            <Burger
              opened={opened}
              onClick={toggle}
              hiddenFrom="sm"
              size="sm"
              aria-label="Abrir navegação"
            />
            <span className="brand-mark" aria-hidden="true">
              ϟ
            </span>
            <Title order={2} size="h3">
              InfraPulse<span className="brand-dot">.</span>
            </Title>
          </Group>
          <Group gap="sm">
            <Badge variant="light" color="gray" visibleFrom="xs">
              API Reliability
            </Badge>
            <ActionIcon
              variant="default"
              size="lg"
              aria-label="Alternar tema"
              onClick={() => setColorScheme(colorScheme === 'dark' ? 'light' : 'dark')}
            >
              {colorScheme === 'dark' ? '☀' : '☾'}
            </ActionIcon>
          </Group>
        </Group>
      </AppShell.Header>
      <AppShell.Navbar p="md">
        <Text size="xs" c="dimmed" fw={700} tt="uppercase" mb="md">
          Workspace
        </Text>
        <Stack gap={6}>
          <NavLink
            component={RouterLink}
            to="/"
            label="Visão geral"
            leftSection={<span aria-hidden="true">▦</span>}
            active={location.pathname === '/'}
            onClick={close}
          />
          <NavLink
            component={RouterLink}
            to="/services"
            label="Serviços"
            leftSection={<span aria-hidden="true">▤</span>}
            active={location.pathname.startsWith('/services')}
            onClick={close}
          />
        </Stack>
        <div className="sidebar-note">
          <Text size="sm" fw={600}>
            Confiabilidade, à vista.
          </Text>
          <Text size="xs" c="dimmed" mt={4}>
            Cada verificação conta uma parte da história.
          </Text>
        </div>
      </AppShell.Navbar>
      <AppShell.Main>
        <main id="main-content" tabIndex={-1}>
          <Suspense fallback={<Loading />}>
            <Outlet />
          </Suspense>
        </main>
      </AppShell.Main>
    </AppShell>
  );
}
