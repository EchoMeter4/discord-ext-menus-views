import os
import math

import discord
from discord.ext import menus


class ViewMenu(menus.Menu):
    def __init__(self, *, auto_defer=True, **kwargs):
        super().__init__(**kwargs)
        self.auto_defer = auto_defer
        self.view = None
        self.__tasks = []

    def button_check(self, interaction):
        return self.ctx.author.id == interaction.user.id

    async def on_menu_button_error(self, exc):
        await self.bot.errors.handle_menu_button_error(exc, self)

    def build_view(self):
        if not self.should_add_reactions():
            return None

        def make_callback(button):
            async def callback(interaction):
                if self.button_check(interaction) is False:
                    return
                if self.auto_defer:
                    await interaction.response.defer()
                try:
                    if button.lock:
                        async with self._lock:
                            if self._running:
                                await button(self, interaction)
                    else:
                        await button(self, interaction)
                except Exception as exc:
                    await self.on_menu_button_error(exc)

            return callback

        view = discord.ui.View(timeout=self.timeout)
        for i, (emoji, button) in enumerate(self.buttons.items()):
            item = discord.ui.Button(style=discord.ButtonStyle.secondary, emoji=emoji, row=i // 5)
            item.callback = make_callback(button)
            view.add_item(item)

        self.view = view
        return view

    def add_button(self, button, *, react=False):
        super().add_button(button)

        if react:
            if self.__tasks:
                async def wrapped():
                    self.buttons[button.emoji] = button
                    try:
                        await self.message.edit(view=self.build_view())
                    except discord.HTTPException:
                        raise

                return wrapped()

            async def dummy():
                raise menus.MenuError("Menu has not been started yet")

            return dummy()

    def remove_button(self, emoji, *, react=False):
        super().remove_button(emoji)

        if react:
            if self.__tasks:
                async def wrapped():
                    self.buttons.pop(emoji, None)
                    try:
                        await self.message.edit(view=self.build_view())
                    except discord.HTTPException:
                        raise

                return wrapped()

            async def dummy():
                raise menus.MenuError("Menu has not been started yet")

            return dummy()

    def clear_buttons(self, *, react=False):
        super().clear_buttons()

        if react:
            if self.__tasks:
                async def wrapped():
                    try:
                        await self.message.edit(view=None)
                    except discord.HTTPException:
                        raise

                return wrapped()

            async def dummy():
                raise menus.MenuError("Menu has not been started yet")

            return dummy()

    async def _internal_loop(self):
        self.__timed_out = False
        try:
            self.__timed_out = await self.view.wait()
        except Exception:
            pass
        finally:
            self._event.set()

            try:
                await self.finalize(self.__timed_out)
            except Exception:
                pass
            finally:
                self.__timed_out = False

            if self.bot.is_closed():
                return

            try:
                if self.delete_message_after:
                    return await self.message.delete()

                if self.clear_reactions_after:
                    return await self.message.edit(view=None)
            except Exception:
                pass

    async def start(self, ctx, *, channel=None, wait=False):
        try:
            del self.buttons
        except AttributeError:
            pass

        self.bot = bot = ctx.bot
        self.ctx = ctx
        self._author_id = ctx.author.id
        channel = channel or ctx.channel
        is_guild = hasattr(channel, "guild")
        me = channel.guild.me if is_guild else ctx.bot.user
        permissions = channel.permissions_for(me)
        self._verify_permissions(ctx, channel, permissions)
        self._event.clear()
        msg = self.message
        if msg is None:
            self.message = msg = await self.send_initial_message(ctx, channel)

        for task in self.__tasks:
            task.cancel()
        self.__tasks.clear()

        self._running = True
        self.__tasks.append(bot.loop.create_task(self._internal_loop()))

        if wait:
            await self._event.wait()

    def send_with_view(self, messageable, *args, **kwargs):
        return messageable.send(*args, **kwargs, view=self.build_view())

    def stop(self):
        self._running = False
        for task in self.__tasks:
            task.cancel()
        self.__tasks.clear()


class ViewMenuPages(menus.MenuPages, ViewMenu):
    def __init__(self, source, **kwargs):
        self._source = source
        self.current_page = 0
        super().__init__(source, **kwargs)

    async def send_initial_message(self, ctx, channel):
        page = await self._source.get_page(0)
        kwargs = await self._get_kwargs_from_page(page)
        return await self.send_with_view(channel, **kwargs)


class IndexMenu(ViewMenu):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.active_menu = None

    async def send_initial_message(self, ctx, channel):
        return await self.send_with_view(ctx, embed=await self.format_index())

    async def format_index(self):
        """Displays the menu embed."""
        raise NotImplementedError()

    async def on_menu_button_error(self, exc):
        await self.bot.errors.handle_menu_button_error(exc, self)

    async def finalize(self, timed_out):
        if self.active_menu is not None:
            self.active_menu.stop()

    def build_view(self):
        if not self.should_add_reactions():
            return None

        def make_callback(button):
            async def callback(interaction):
                if self.button_check(interaction) is False:
                    return
                if self.auto_defer:
                    await interaction.response.defer()
                try:
                    if button.lock:
                        async with self._lock:
                            if self._running:
                                await button(self, interaction)
                    else:
                        await button(self, interaction)
                except Exception as exc:
                    await self.on_menu_button_error(exc)

            return callback

        view = discord.ui.View(timeout=self.timeout)
        for i, (emoji, button) in enumerate(self.buttons.items()):
            item = discord.ui.Button(style=discord.ButtonStyle.secondary, emoji=emoji, row=i // 5,
                                     custom_id=f"indexmenu:{os.urandom(16).hex()}")
            item.callback = make_callback(button)
            view.add_item(item)

        self.view = view
        return view


class SubMenuPages(ViewMenuPages):

    def __init__(self, source, *, parent_menu: IndexMenu, **kwargs):
        self._source = source
        self.parent_menu = parent_menu
        super().__init__(message=parent_menu.message, source=source, clear_reactions_after=False, **kwargs)
        self.show_index = True
        self.clean_up_buttons()

    def clean_up_buttons(self):
        self.remove_button('\N{BLACK SQUARE FOR STOP}\ufe0f')

    async def start(self, ctx, *, channel=None, wait=False):
        await super().start(ctx, channel=None, wait=False)
        await self.message.edit(view=self.build_view())
        await self.show_page(0)

    def build_restored_parent_view(self):
        # Make a brand new view with the parent items since it wouldn't work using the original one.
        view = discord.ui.View(timeout=self.parent_menu.timeout)

        for item in self.view.children:
            if item.custom_id.startswith("indexmenu"):
                view.add_item(item)

        return view

    def stop(self, *, show_index: bool = False):
        """Make sure to set show_index to true on an index calling button."""
        self.show_index = show_index
        super().stop()

    async def finalize(self, timed_out):
        if timed_out or not self.show_index:
            return

        self.parent_menu.view = view = self.build_restored_parent_view()
        self.parent_menu.active_menu = None

        await self.message.edit(view=view, embed=await self.parent_menu.format_index())

    def build_view(self):
        if not self.should_add_reactions():
            view = discord.ui.View(timeout=self.parent_menu.timeout)

            for item in self.parent_menu.view.children:
                if item.custom_id.startswith('indexmenu'):
                    view.add_item(item)

            self.view = self.parent_menu.view = view
            return self.parent_menu.view

        def make_callback(button):
            async def callback(interaction):
                if self.button_check(interaction) is False:
                    return
                if self.auto_defer:
                    await interaction.response.defer()
                try:
                    if button.lock:
                        async with self._lock:
                            if self._running:
                                await button(self, interaction)
                    else:
                        await button(self, interaction)
                except Exception as exc:
                    await self.on_menu_button_error(exc)

            return callback

        # Brand new View Object so the original one stays intact for later use.
        view = discord.ui.View(timeout=self.parent_menu.timeout)

        for item in self.parent_menu.view.children:
            if item.custom_id.startswith('indexmenu'):
                view.add_item(item)

        print((len(view.children) // 5) * 5)
        for i, (emoji, button) in enumerate(self.buttons.items(), start=math.ceil(len(view.children) / 5) * 5):
            print(i // 5)
            item = discord.ui.Button(style=discord.ButtonStyle.secondary, emoji=emoji, row=i // 5)
            item.callback = make_callback(button)
            view.add_item(item)

        self.view = self.parent_menu.view = view
        return view


class SubMenu(ViewMenu):

    def __init__(self, *, parent_menu: IndexMenu, **kwargs):
        self.parent_menu = parent_menu
        super().__init__(message=parent_menu.message, clear_reactions_after=False, **kwargs)
        self.show_index = True
        self.clean_up_buttons()

    async def send_initial_message(self, ctx, **kwargs):
        raise Exception('This menu does not support send_initial_message')

    async def get_initial_embed(self):
        """Returns the embed that will be shown once the menu is started."""
        raise NotImplementedError

    def clean_up_buttons(self):
        self.remove_button('\N{BLACK SQUARE FOR STOP}\ufe0f')

    async def start(self, ctx, *, channel=None, wait=False):
        await super().start(ctx, channel=channel, wait=wait)
        await self.message.edit(embed=await self.get_initial_embed(), view=self.build_view())

    def build_restored_parent_view(self):
        # Make a brand new view with the parent items since it wouldn't work using the original one.
        view = discord.ui.View(timeout=self.parent_menu.timeout)

        for item in self.view.children:
            if item.custom_id.startswith("indexmenu"):
                view.add_item(item)

        return view

    def stop(self, *, show_index: bool = False):
        """Make sure to set show_index to true on an index calling button."""
        self.show_index = show_index
        super().stop()

    async def finalize(self, timed_out):
        if timed_out or not self.show_index:
            return

        self.parent_menu.view = view = self.build_restored_parent_view()
        self.parent_menu.active_menu = None

        await self.message.edit(view=view, embed=await self.parent_menu.format_index())

    def build_view(self):
        if not self.should_add_reactions():
            view = discord.ui.View(timeout=self.parent_menu.timeout)

            for item in self.parent_menu.view.children:
                if item.custom_id.startswith('indexmenu'):
                    view.add_item(item)

            self.view = self.parent_menu.view = view
            return self.parent_menu.view

        def make_callback(button):
            async def callback(interaction):
                if self.button_check(interaction) is False:
                    return
                if self.auto_defer:
                    await interaction.response.defer()
                try:
                    if button.lock:
                        async with self._lock:
                            if self._running:
                                await button(self, interaction)
                    else:
                        await button(self, interaction)
                except Exception as exc:
                    await self.on_menu_button_error(exc)

            return callback

        # Brand new View Object so the original one stays intact for later use.
        view = discord.ui.View(timeout=self.parent_menu.timeout)

        for item in self.parent_menu.view.children:
            if item.custom_id.startswith('indexmenu'):
                view.add_item(item)

        for i, (emoji, button) in enumerate(self.buttons.items(), start=math.ceil(len(view.children) / 5) * 5):
            item = discord.ui.Button(style=discord.ButtonStyle.secondary, emoji=emoji, row=i // 5)
            item.callback = make_callback(button)
            view.add_item(item)

        self.view = self.parent_menu.view = view
        return view
