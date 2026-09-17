# Пользователи и группы AD для PVS

Подключение созданных групп к вашему Kolla-Ansible описано в
[инструкции по интеграции AD с OpenStack и OpenSearch](AD-INTEGRATION.md).
Она содержит точные пути, конфигурационные примеры и проверки прав.

`Create-PvsAdAccounts.ps1` создаёт три пользователя и три группы безопасности
типа Global в существующей OU. Каждый пользователь включается в свою группу.

| Пользователь | Группа AD | Назначение по ролевой модели |
| --- | --- | --- |
| `virtualization_admin` | `PVS_VirtAdmins` | Роль OpenStack `virtualization_admin` с наследованием `admin` в назначенном домене и проекте |
| `security_admin` | `PVS_SecurityAudit` | Роль OpenSearch `security_admin`: аудит журналов |
| `vm_developer` | `PVS_VMDevelopers` | Роль OpenStack `vm_developer`: разработчик ВМ |

## Требования

- Windows PowerShell 5.1 и модуль `ActiveDirectory` из RSAT либо средств управления AD DS.
- Доступный контроллер домена с возможностью записи и заранее созданная OU.
- Запуск от учётной записи с правами создания пользователей, групп и изменения
  членства в этой OU. Права Domain Admin не обязательны при делегировании этих операций.
- Указанные имена пользователей и групп ещё не заняты в домене.

## Запуск

Скачайте скрипт, откройте PowerShell в его каталоге и выполните:

```powershell
.\Create-PvsAdAccounts.ps1 -Server "dc01.example.local" -OU "OU=PVS,DC=example,DC=local"
```

Замените `dc01.example.local` на FQDN своего контроллера домена, а `OU` — на полный
Distinguished Name существующей OU. Домен для UPN скрипт получает автоматически
с указанного контроллера; например, получится `vm_developer@example.local`.

Введите отдельный пароль для каждого пользователя. Ввод скрыт, пароли не записываются
в скрипт и должны соответствовать политике AD. Пользователи создаются включёнными,
без обязательной смены пароля при следующем входе; бессрочные пароли не устанавливаются.
После создания скрипт выводит UPN пользователя и DN его группы.

Скрипт предназначен для первичного создания. Если любое из шести имён уже существует,
он останавливается до создания объектов. При ошибке во время создания уже созданные
объекты остаются в AD: автоматического отката и продолжения повторным запуском нет.
Например, при отклонении пароля политикой AD может остаться отключённый пользователь.

## Проверка членства

Укажите тот же контроллер домена:

```powershell
$server = "dc01.example.local"
'PVS_VirtAdmins', 'PVS_SecurityAudit', 'PVS_VMDevelopers' | ForEach-Object {
    Write-Host "Group: $_"
    Get-ADGroupMember -Identity $_ -Server $server | Select-Object SamAccountName
}
```

В каждой группе должен находиться соответствующий пользователь из таблицы.

## Назначение прав в приложениях

Создание группы AD само по себе не назначает права OpenStack или OpenSearch.
После настройки интеграции с AD по LDAPS сопоставьте группы с прикладными ролями:

- `PVS_VirtAdmins` — с `virtualization_admin` в нужном домене/проекте OpenStack.
- `PVS_VMDevelopers` — с `vm_developer` в нужном проекте OpenStack.
  Предусмотренный моделью запрет удаления ВМ должен обеспечиваться политиками OpenStack;
  одно наследование `member`/`reader` этот запрет не реализует.
- `PVS_SecurityAudit` — с `security_admin` в OpenSearch: чтение, поиск и экспорт
  событий `flog-*`, работа с поисками и dashboards в tenant `security-audit`.
  Изменение/удаление событий, управление индексами, пользователями, ролями
  и конфигурацией OpenSearch должны быть запрещены.

Скрипт не настраивает LDAPS, прикладные роли и политики. Включение пользователей
в административные группы самого AD не выполняется.

## Проверка скрипта

Выполнена статическая проверка файла. Запуск в Windows PowerShell, создание объектов
в AD и вход в OpenStack/OpenSearch в рамках подготовки не проверялись.

## Документация

- [Microsoft: New-ADUser](https://learn.microsoft.com/en-us/powershell/module/activedirectory/new-aduser)
- [Microsoft: New-ADGroup](https://learn.microsoft.com/en-us/powershell/module/activedirectory/new-adgroup)
- [Microsoft: Add-ADGroupMember](https://learn.microsoft.com/en-us/powershell/module/activedirectory/add-adgroupmember)
- [Keystone: интеграция с LDAP](https://docs.openstack.org/keystone/latest/admin/configuration.html#integrate-identity-with-ldap)
- [OpenSearch: Active Directory и LDAP](https://docs.opensearch.org/latest/security/authentication-backends/ldap/)
